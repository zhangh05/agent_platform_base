/**
 * useChatStream — encapsulates the WebSocket streaming + HTTP fallback logic
 * previously inlined in AgentWorkbench.tsx's onSend function (~440 lines).
 *
 * Owns the message-level WebSocket lifecycle, the per-token batch flush, the
 * SSOT Runtime stage event handling, and the error/timeout paths. Delegates
 * persistent state writes back to the workbench store via injected callbacks
 * so the page component stays in control of session routing.
 *
 * System-level WebSocket (systemWsRef) and the per-page UI refs (chat scroll,
 * input element) stay in the page component — this hook is only responsible
 * for one turn at a time.
 */

import { useCallback, useEffect, useRef } from "react";
import { agentApi, jobsApi, sessionsApi } from "../api";
import { getApiAccessToken, realtimeEndpoint } from "../api/client";
import { useWorkbenchStore } from "../stores/workbench";
import { useSessionStore } from "../stores/session";
import { isApiError } from "../types";
import type { AgentResult, ToolCallResult, InlineToolCall, CognitiveSummary, CognitiveEvent } from "../types";
import { sanitizeAssistantText, toolLabel, filterStreamingThink, type ThinkFilterState } from "../utils/displayText";
import { beginModelStep, canFallbackToHttp, discardToolCallDraft, finalizeStreamText } from "../utils/agentStream";
import { agentResultFromWsDone } from "../utils/wsResult";
import { notifyRunCompleted } from "../utils/appEvents";
import { createStreamActivityWatchdog, STREAM_IDLE_TIMEOUT_MS } from "../utils/streamActivity";
import { decideStreamFrame } from "../utils/streamSequence";
import { progressPatchForStreamStage, stageElapsedSince } from "../utils/streamStage";

const WS_TIMEOUT_MS = 3000;
// Rendering Markdown is substantially more expensive than receiving tokens.
// Five text-node updates per second looks fluid and leaves enough main-thread
// time for input, navigation and scrolling during long responses.
const TOKEN_FLUSH_MS = 200;

export type ChatStreamAttachment = {
  file_id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  kind: "image" | "file";
  previewUrl?: string;
};

export type ChatStreamCallbacks = {
  /** Called when a new server-side session id is created; the page should
   *  update useSessionStore + switchSession + close the mobile drawer. */
  onSessionResolved: (sessionId: string) => void;
  /** Called with the finalized AgentResult; the page may use this to show
   *  a toast (success / failure). Default no-op. */
  onResult?: (result: AgentResult, scratchSessionId: string) => void;
  /** Called when the WS stream is interrupted without a terminal frame. */
  onInterruption?: (message: string) => void;
};

export type ChatStreamParams = {
  /** The active workspace id, or null if no workspace is selected. */
  workspaceId: string | null;
  /** The current session id, or null if the page is in a fresh state. */
  sessionId: string | null;
  /** Model vision capability — blocks sends that include images. */
  llmHealth: { visionSupported?: boolean };
};

export type ChatStreamReturn = {
  /** True while a turn is in flight; mirrors useWorkbenchStore.sending so
   *  pages can either read it from the hook or the store directly. */
  sending: boolean;
  send: (args: {
    text: string;
    attachments: ChatStreamAttachment[];
    /** Already-resolved session id (after attachment upload). */
    effectiveSessionId: string | null;
    /** Extra metadata merged into the turn payload (e.g. auto-prompt context). */
    turnMetadata?: Record<string, unknown>;
  }) => Promise<void>;
  stop: () => void;
};

export function useChatStream(
  params: ChatStreamParams,
  callbacks: ChatStreamCallbacks
): ChatStreamReturn {
  const { workspaceId } = params;
  const { onSessionResolved, onResult, onInterruption } = callbacks;

  const sending = useWorkbenchStore((s) => s.sending);
  const abortRef = useRef<AbortController | null>(null);
  const msgWsRef = useRef<WebSocket | null>(null);
  // Refs make start/stop atomic across React render scheduling.
  const sendInFlightRef = useRef(false);
  const stopRequestedRef = useRef(false);
  const activeClientRequestIdRef = useRef("");

  // Stable refs so the send function identity is stable across renders.
  const paramsRef = useRef(params);
  paramsRef.current = params;
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  const requestCancel = (jobId: string, workspaceId: string) => {
    void jobsApi.cancel(jobId, workspaceId).catch(() => {});
  };

  const stopStream = (): void => {
    const currentSessionId = paramsRef.current.sessionId;
    const workspaceId = paramsRef.current.workspaceId;
    const currentMessages = currentSessionId
      ? useWorkbenchStore.getState().bySession[currentSessionId] || []
      : [];
    const activeJobId = [...currentMessages].reverse().find(
      (message) => message.role === "assistant" && message.status === "streaming" && message.activeJobId,
    )?.activeJobId;
    stopRequestedRef.current = true;
    if (activeJobId && workspaceId) {
      requestCancel(activeJobId, workspaceId);
    } else if (workspaceId && activeClientRequestIdRef.current) {
      // The job is created asynchronously. Resolve it by the client request id
      // instead of closing the socket and silently leaving the turn running.
      const clientRequestId = activeClientRequestIdRef.current;
      void (async () => {
        for (let attempt = 0; attempt < 6 && stopRequestedRef.current; attempt += 1) {
          try {
            const jobs = await jobsApi.list(workspaceId);
            const job = (jobs.jobs || []).find((item) =>
              item.status === "running"
              && item.metadata?.active_turn?.client_request_id === clientRequestId,
            );
            if (job?.job_id) {
              requestCancel(job.job_id, workspaceId);
              return;
            }
          } catch { /* best effort: the durable job state remains authoritative */ }
          await new Promise((resolve) => window.setTimeout(resolve, 150));
        }
      })();
    }
    // Keep the message socket open: the canonical cancellation path can then
    // deliver its real terminal outcome rather than being rendered as a network failure.
  };

  // A route change must not leave an in-flight socket or global sending state
  // behind. Otherwise returning to the workbench can look permanently frozen.
  useEffect(() => () => {
    abortRef.current?.abort();
    abortRef.current = null;
    try { msgWsRef.current?.close(); } catch { /* noop */ }
    msgWsRef.current = null;
    useWorkbenchStore.getState().setSending(false);
  }, []);

  const send = useCallback(async (args: {
    text: string;
    attachments: ChatStreamAttachment[];
    effectiveSessionId: string | null;
    turnMetadata?: Record<string, unknown>;
  }): Promise<void> => {
    const { text, attachments, turnMetadata: metadataOverride = {} } = args;
    const ws = paramsRef.current;
    if (!ws.workspaceId) return;
    if (sending || sendInFlightRef.current) return;
    const activeSessionId = ws.sessionId;
    const effectiveSessionId = args.effectiveSessionId ?? activeSessionId;
    // Sessions are explicit UI/runtime scope. Never recreate the retired
    // scratch path or let the backend implicitly invent a conversation.
    if (!effectiveSessionId) return;

    const hasImages = attachments.some((a) => a.mime_type.startsWith("image/"));
    if (hasImages && ws.llmHealth.visionSupported === false) return;

    sendInFlightRef.current = true;
    stopRequestedRef.current = false;

    // Reads that decide where a turn belongs must use the current render's
    // session snapshot, not a value captured by an earlier send callback.
    const scratch = effectiveSessionId;

    // Append the user + streaming placeholder so the page can render immediately.
    const store = useWorkbenchStore.getState();
    store.appendUser(text, scratch, attachments.length ? attachments : undefined);
    const streamingMsgId = store.appendAssistantStreaming(scratch);
    useWorkbenchStore.getState().setSending(true);

    const fullText = text;
    const clientRequestId = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `request-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const turnMetadata = { ...metadataOverride, client_request_id: clientRequestId };
    activeClientRequestIdRef.current = clientRequestId;

    // Try WebSocket streaming first, fall back to HTTP.
    const wsUrl = realtimeEndpoint("/ws/agent");
    abortRef.current = new AbortController();

    let wsTurnSubmitted = false;
    try {
      const socket = new WebSocket(wsUrl);
      msgWsRef.current = socket;

      // Token batching — buffer tokens, flush at a UI-friendly cadence.
      const tokenBufferRef = { pending: "" };
      const thinkFilter: { mode: ThinkFilterState } = { mode: "idle" };
      let streamState = beginModelStep();
      let streamedText = "";
      let resolvedSid: string = activeSessionId || "";
      let stageStartedAt: number | null = null;

      const wsReady: Promise<void> = new Promise((resolve, reject) => {
        const timer = setTimeout(() => { reject(new Error("ws_timeout")); }, WS_TIMEOUT_MS);
        socket.onopen = () => { clearTimeout(timer); resolve(); };
        socket.onerror = () => { clearTimeout(timer); reject(new Error("ws_error")); };
      });
      await wsReady;

      wsTurnSubmitted = true;
      socket.send(JSON.stringify({
        type: "message",
        user_input: fullText,
        session_id: effectiveSessionId,
        workspace_id: ws.workspaceId,
        metadata: turnMetadata,
        auth_token: getApiAccessToken(),
      }));

      const streamingResult: {
        session_id?: string;
        turn_id?: string;
        trace_id?: string;
        events?: AgentResult["events"];
        tool_calls_count?: number;
        tool_calls?: ToolCallResult[];
        metadata?: AgentResult["metadata"];
        errors?: string[];
        warnings?: string[];
        tool_decision?: AgentResult["tool_decision"];
        no_tool_reason?: string;
      } = {};
      let terminalFrameReceived = false;
      let lastStreamSequence = 0;
      let interruptionReason = "";

      await new Promise<void>((resolve) => {
        const flushTokenBuffer = () => {
          if (!tokenBufferRef.pending) return;
          streamState.draft += tokenBufferRef.pending;
          streamedText = streamState.draft;
          tokenBufferRef.pending = "";
          useWorkbenchStore.getState().updateAssistant(
            streamingMsgId, { text: streamedText }, scratch,
          );
        };
        const flushTimer = setInterval(flushTokenBuffer, TOKEN_FLUSH_MS);
        let finished = false;

        const finish = () => {
          if (finished) return;
          finished = true;
          watchdog.stop();
          clearInterval(flushTimer);
          flushTokenBuffer();
          resolve();
        };

        const watchdog = createStreamActivityWatchdog({
          onTick: (elapsedMs) => {
            const stageElapsedMs = stageElapsedSince(stageStartedAt);
            useWorkbenchStore.getState().updateAssistant(
              streamingMsgId, { progressElapsedMs: elapsedMs, stageElapsedMs }, scratch,
            );
          },
          onTimeout: () => {
            interruptionReason = `实时连接已超过 ${Math.round(STREAM_IDLE_TIMEOUT_MS / 1000)} 秒没有收到服务器消息，已结束等待。请重试本轮。`;
            try { socket.close(); } catch { /* already closed */ }
            finish();
          },
        });

        useWorkbenchStore.getState().updateAssistant(
          streamingMsgId, { progressText: "等待 SSOT Runtime 调度…" }, scratch,
        );

        socket.onmessage = (event) => {
          watchdog.touch();
          try {
            const msg = JSON.parse(event.data);
            const sequenceDecision = decideStreamFrame(msg, lastStreamSequence, terminalFrameReceived);
            if (!sequenceDecision.accept) return;
            lastStreamSequence = sequenceDecision.nextSequence;
            switch (msg.type) {
              case "token": {
                const raw = msg.content || "";
                const visible = filterStreamingThink(raw, thinkFilter);
                tokenBufferRef.pending += visible;
                break;
              }
              case "event": {
                // Heartbeats prove transport liveness but are not runtime
                // evidence and must not pollute persisted/inspected events.
                if (msg.data && msg.name !== "heartbeat") {
                  streamingResult.events = [...(streamingResult.events || []), msg.data];
                }
                const stageName = msg.name as string;
                if (stageName.startsWith("cognitive_") && msg.data) {
                  const rawPayload = msg.data.payload;
                  const payload = rawPayload && typeof rawPayload === "object"
                    ? rawPayload as Record<string, unknown>
                    : {};
                  const previous: CognitiveSummary = streamingResult.metadata?.cognitive ?? {};
                  const nextSummary: CognitiveSummary = {
                    ...previous,
                    revision: Number(msg.data.state_revision ?? previous.revision ?? 0) || previous.revision,
                    ...(typeof payload.goal === "string" ? { goal: payload.goal } : {}),
                    ...(typeof payload.outcome === "string" ? { outcome: payload.outcome } : {}),
                    ...(typeof payload.visible_summary === "string" ? { visible_summary: payload.visible_summary } : {}),
                    ...(typeof payload.decision === "string"
                      ? { decision: { ...previous.decision, decision: payload.decision, visible_summary: String(payload.visible_summary || previous.decision?.visible_summary || "") } }
                      : {}),
                  };
                  const priorEvents = streamingResult.metadata?.cognitive_events ?? [];
                  const cognitiveEvent = msg.data as CognitiveEvent;
                  streamingResult.metadata = {
                    ...(streamingResult.metadata ?? {}),
                    cognitive: nextSummary,
                    cognitive_events: priorEvents.some((item) => item.event_id === cognitiveEvent.event_id)
                      ? priorEvents
                      : [...priorEvents, cognitiveEvent],
                  };
                }
                if (msg.data && stageName !== "heartbeat") {
                  const storeState = useWorkbenchStore.getState();
                  const currentMessage = storeState.bySession[scratch]?.find((item) => item.id === streamingMsgId);
                  const runtimeEvent = {
                    ...msg.data,
                    event_id: String(msg.data.event_id || `live-${clientRequestId}-${msg.seq || Date.now()}`),
                    event_type: String(msg.data.event_type || msg.data.type || stageName),
                    type: String(msg.data.type || stageName),
                  };
                  storeState.updateAssistant(streamingMsgId, {
                    runtimeEvents: [...(currentMessage?.runtimeEvents || []), runtimeEvent],
                    activeJobId: String(msg.data.job_id || currentMessage?.activeJobId || "") || undefined,
                  }, scratch);
                }
                if (stageName === "model_started") {
                  streamState = beginModelStep(streamedText);
                  streamedText = "";
                  useWorkbenchStore.getState().updateAssistant(streamingMsgId, { text: "" }, scratch);
                }
                const progressPatch = progressPatchForStreamStage(stageName, msg.data);
                if (progressPatch) {
                  const stageElapsedMs = progressPatch.stageElapsedMs ?? 0;
                  stageStartedAt = Date.now() - stageElapsedMs;
                  useWorkbenchStore.getState().updateAssistant(
                    streamingMsgId,
                    progressPatch,
                    scratch,
                  );
                }
                if (stopRequestedRef.current && msg.data?.job_id && workspaceId) {
                  requestCancel(String(msg.data.job_id), workspaceId);
                }
                if (stageName === "tool_call" || stageName === "tool_result") {
                  streamingResult.tool_calls_count = (streamingResult.tool_calls_count || 0) + 1;
                  const tid = msg.data?.tool_id || msg.data?.name || "";
                  const callId = String(msg.data?.call_id || msg.data?.node_id || tid || "");
                  if (tid && callId) {
                    const storeState = useWorkbenchStore.getState();
                    const curr = storeState.bySession[scratch]?.find((m) => m.id === streamingMsgId);
                    const prevCalls = (curr?.toolCalls || []) as InlineToolCall[];
                    if (stageName === "tool_result") {
                      const ok = msg.data?.ok ?? msg.data?.status === "ok";
                      const nextCalls = prevCalls.map((t) =>
                        t.call_id === callId ? { ...t, status: ok ? "done" : "fail", ok, summary: msg.data?.summary } : t,
                      );
                      storeState.updateAssistant(streamingMsgId, { toolCalls: nextCalls }, scratch);
                    } else if (!prevCalls.find((t) => t.call_id === callId)) {
                      storeState.updateAssistant(streamingMsgId, {
                        toolCalls: [...prevCalls, { call_id: callId, tool_id: tid, tool_name: toolLabel(tid), ok: false, status: "running" }],
                      }, scratch);
                    }
                  }
                }
                if (stageName === "tool_call") {
                  flushTokenBuffer();
                  discardToolCallDraft(streamState);
                  streamedText = "";
                  useWorkbenchStore.getState().updateAssistant(streamingMsgId, { text: "" }, scratch);
                }
                break;
              }
              case "done": {
                terminalFrameReceived = true;
                // The final token batch can arrive less than TOKEN_FLUSH_MS before done.
                // Fold it into the draft before choosing between draft and final_response.
                flushTokenBuffer();
                resolvedSid = msg.session_id || activeSessionId || "";
                streamedText = finalizeStreamText(streamState.draft, msg.final_response || "");
                streamingResult.session_id = msg.session_id;
                streamingResult.turn_id = msg.turn_id;
                streamingResult.trace_id = msg.trace_id;
                streamingResult.events = msg.events || streamingResult.events || [];
                streamingResult.tool_calls_count = msg.tool_calls_count || streamingResult.tool_calls_count;
                streamingResult.tool_calls = msg.tool_calls || [];
                streamingResult.metadata = { ...(streamingResult.metadata ?? {}), ...(msg.metadata || {}) };
                streamingResult.errors = msg.errors || [];
                streamingResult.warnings = msg.warnings || [];
                streamingResult.tool_decision = msg.tool_decision;
                streamingResult.no_tool_reason = msg.no_tool_reason;
                useWorkbenchStore.getState().updateAssistant(
                  streamingMsgId, { progressText: "" }, scratch,
                );
                finish();
                break;
              }
              case "error": {
                terminalFrameReceived = true;
                streamingResult.errors = [msg.message || msg.error || "Unknown error"];
                useWorkbenchStore.getState().updateAssistant(
                  streamingMsgId, { progressText: "" }, scratch,
                );
                finish();
                break;
              }
            }
          } catch { /* ignore parse errors */ }
        };

        socket.onclose = () => {
          if (tokenBufferRef.pending || streamState.draft !== streamedText) {
            streamState.draft += tokenBufferRef.pending;
            streamedText = streamState.draft;
            tokenBufferRef.pending = "";
            useWorkbenchStore.getState().updateAssistant(
              streamingMsgId, { text: streamedText }, scratch,
            );
          }
          finish();
        };
        socket.onerror = () => {
          if (tokenBufferRef.pending || streamState.draft !== streamedText) {
            streamState.draft += tokenBufferRef.pending;
            streamedText = streamState.draft;
            tokenBufferRef.pending = "";
            useWorkbenchStore.getState().updateAssistant(
              streamingMsgId, { text: streamedText }, scratch,
            );
          }
          finish();
        };
      });

      if (!terminalFrameReceived) {
        const interruption = interruptionReason || "实时连接已中断，未收到本轮完成消息。请重试。";
        streamingResult.errors = [interruption];
        if (!streamedText.trim()) streamedText = interruption;
        onInterruption?.(interruption);
      } else if (streamingResult.errors?.length && !streamedText.trim()) {
        streamedText = streamingResult.errors[0];
      }

      try { socket.close(); } catch { /* already closed */ }
      msgWsRef.current = null;

      // Resolve new-session routing before writing the final assistant text.
      if (!activeSessionId && resolvedSid) {
        useWorkbenchStore.getState().moveSessionMessages("_scratch", resolvedSid);
        useSessionStore.getState().setCurrentSession(resolvedSid);
        useWorkbenchStore.getState().switchSession(resolvedSid);
        onSessionResolved(resolvedSid);
      }

      const wsResult = agentResultFromWsDone(streamingResult, streamedText, resolvedSid);
      const cleanText = sanitizeAssistantText(wsResult.final_response);
      const cleanResult = { ...wsResult, final_response: sanitizeAssistantText(wsResult.final_response ?? "") };
      const toolCalls: InlineToolCall[] = (cleanResult.tool_calls ?? []).map((tc) => ({
        call_id: tc.call_id,
        tool_id: tc.tool_id,
        tool_name: toolLabel(tc.tool_id),
        ok: tc.ok,
        summary: tc.summary,
        duration_ms: tc.duration_ms ?? undefined,
        errors: tc.errors,
        artifacts: tc.artifacts as InlineToolCall["artifacts"],
        orchestration: (tc.metadata?.orchestration || undefined) as InlineToolCall["orchestration"],
      }));
      useWorkbenchStore.getState().updateAssistant(streamingMsgId, {
        status: wsResult.errors?.length ? "error" : "ready",
        text: cleanText,
        result: cleanResult,
        toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
        error: wsResult.errors?.[0],
        trace_id: wsResult.trace_id,
        run_id: wsResult.turn_id,
        runtimeEvents: cleanResult.events || [],
      }, resolvedSid);

      // Defer heavy post-processing.
      queueMicrotask(() => {
        useWorkbenchStore.getState().setLatestResult(wsResult, resolvedSid);
        notifyRunCompleted();
        onResult?.(wsResult, scratch);
      });

      if (resolvedSid && workspaceId) {
        sessionsApi.messages(resolvedSid, workspaceId)
          .then((r) => { if (r.messages?.length) useWorkbenchStore.getState().mergeFromBackend(resolvedSid, r.messages); })
          .catch(() => {});
      }
      return;
    } catch {
      if (msgWsRef.current) { try { msgWsRef.current.close(); } catch { /* noop */ } }
      if (!canFallbackToHttp(wsTurnSubmitted)) {
        const interruption = "实时连接在请求提交后中断；为避免重复执行，未自动重放。可等待会话任务恢复或手动重试。";
        useWorkbenchStore.getState().updateAssistant(streamingMsgId, {
          status: "error", text: interruption, error: interruption,
        }, effectiveSessionId);
        onInterruption?.(interruption);
        return;
      }
      // No turn frame was submitted, so HTTP is a safe initial transport fallback.
      if (!workspaceId) return;
      try {
        const res = await agentApi.run({
          message: fullText,
          workspace_id: workspaceId,
          session_id: effectiveSessionId ?? undefined,
          metadata: turnMetadata,
        });
        const resolvedSid = (res.session_id && res.session_id !== "—" ? res.session_id : activeSessionId) ?? undefined;
        if (!activeSessionId && resolvedSid) {
          useWorkbenchStore.getState().moveSessionMessages("_scratch", resolvedSid);
          useSessionStore.getState().setCurrentSession(resolvedSid);
          useWorkbenchStore.getState().switchSession(resolvedSid);
          onSessionResolved(resolvedSid);
        }
        const tcArray = (res.tool_calls ?? []).map((tc: ToolCallResult) => ({
          call_id: tc.call_id, tool_id: tc.tool_id, tool_name: toolLabel(tc.tool_id), ok: tc.ok,
          summary: tc.summary, duration_ms: tc.duration_ms ?? undefined,
          errors: tc.errors, artifacts: tc.artifacts,
          orchestration: (tc.metadata?.orchestration || undefined) as InlineToolCall["orchestration"],
        }));
        useWorkbenchStore.getState().updateAssistant(streamingMsgId, {
          status: res.ok ? "ready" : "error",
          text: sanitizeAssistantText(res.final_response ?? ""),
          result: res,
          toolCalls: tcArray.length > 0 ? tcArray : undefined,
          error: !res.ok ? res.errors?.[0] : undefined,
          trace_id: res.trace_id,
          run_id: res.turn_id,
        }, resolvedSid);
        useWorkbenchStore.getState().setLatestResult(res, resolvedSid);
        notifyRunCompleted();
        onResult?.(res, scratch);
        if (resolvedSid && workspaceId) {
          sessionsApi.messages(resolvedSid, workspaceId)
            .then((r) => { if (r.messages?.length) useWorkbenchStore.getState().mergeFromBackend(resolvedSid, r.messages); })
            .catch(() => { /* background sync is best-effort */ });
        }
      } catch (err: unknown) {
        const msg = isApiError(err) ? err.message : String(err);
        const fallbackSid = effectiveSessionId;
        const stubResult: AgentResult = {
          ok: false, final_response: sanitizeAssistantText(`(error) ${msg}`),
          events: [], trace_id: isApiError(err) ? err.request_id ?? "—" : "—",
          session_id: fallbackSid ?? "—", turn_id: `turn-${Date.now()}`,
          tool_calls: [], warnings: [], errors: [msg], error_type: "network",
          metadata: { source_count: 0, source_summary: [] },
        };
        useWorkbenchStore.getState().updateAssistant(streamingMsgId, {
          status: "error", text: stubResult.final_response,
          result: stubResult, error: msg,
        }, fallbackSid);
        useWorkbenchStore.getState().setLatestResult(stubResult, fallbackSid);
        onResult?.(stubResult, fallbackSid);
      }
    } finally {
      msgWsRef.current = null;
      activeClientRequestIdRef.current = "";
      stopRequestedRef.current = false;
      sendInFlightRef.current = false;
      useWorkbenchStore.getState().setSending(false);
    }
  }, [sending, workspaceId, onSessionResolved, onResult, onInterruption]);

  return { sending, send, stop: stopStream };
}
