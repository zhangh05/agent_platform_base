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
import { agentApi, sessionsApi } from "../api";
import { getApiAccessToken } from "../api/client";
import { useWorkbenchStore } from "../stores/workbench";
import { useSessionStore } from "../stores/session";
import { isApiError } from "../types";
import type { AgentResult, ToolCallResult, InlineToolCall } from "../types";
import { sanitizeAssistantText, toolLabel, filterStreamingThink, type ThinkFilterState } from "../utils/displayText";
import { beginModelStep, discardToolCallDraft, finalizeStreamText } from "../utils/agentStream";
import { agentResultFromWsDone } from "../utils/wsResult";
import { notifyRunCompleted } from "../utils/appEvents";
import { createStreamActivityWatchdog, STREAM_IDLE_TIMEOUT_MS } from "../utils/streamActivity";
import {
  clearInflightChatStream,
  newChatStreamId,
  readInflightChatStream,
  updateInflightChatStream,
  writeInflightChatStream,
  type InflightChatStream,
} from "../utils/chatStreamRecovery";

const WS_TIMEOUT_MS = 3000;
const TOKEN_FLUSH_MS = 50;
const WS_RESUME_ATTEMPTS = 8;

// Stage label table mirrors core.runtime_engine/stage_events.py
const STAGE_LABELS: Record<string, string> = {
  turn_started:        "开始处理",
  planner_started:     "正在分析任务…",
  planner_completed:   "已规划执行图",
  graph_compiled:      "构建执行图…",
  structural_validated:"图结构校验通过",
  semantic_validated:  "语义校验通过",
  semantic_invalid:    "语义校验发现问题",
  pre_repair_started:  "自动修复阶段…",
  pre_repair_completed:"已自动修复",
  risk_assessed:       "风险评估完成",
  budget_ok:           "预算检查通过",
  execution_started:   "开始执行工具…",
  execution_completed: "工具执行完成",
  orchestration_planned: "已生成动态执行计划",
  orchestration_layer_started: "正在执行协同步骤…",
  orchestration_layer_completed: "协同步骤执行完成",
  repair_attempt:      "重试节点",
  merge_completed:     "汇总执行结果",
  response_started:    "整理回复…",
  response_completed:  "回复已就绪",
  turn_completed:      "处理完成",
  heartbeat:           "仍在处理…",
};

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
  const detachedRef = useRef(false);
  const manualStopRef = useRef(false);

  // Stable refs so the send function identity is stable across renders.
  const paramsRef = useRef(params);
  paramsRef.current = params;
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  const stopStream = (): void => {
    manualStopRef.current = true;
    const inflight = readInflightChatStream();
    if (inflight) {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      try {
        const cancelSocket = new WebSocket(`${protocol}//${window.location.host}/ws/agent`);
        const closeTimer = setTimeout(() => cancelSocket.close(), WS_TIMEOUT_MS);
        cancelSocket.onopen = () => cancelSocket.send(JSON.stringify({
          type: "cancel",
          stream_id: inflight.streamId,
          session_id: inflight.sessionId,
          workspace_id: inflight.workspaceId,
          auth_token: getApiAccessToken(),
        }));
        cancelSocket.onmessage = () => { clearTimeout(closeTimer); cancelSocket.close(); };
        cancelSocket.onerror = () => clearTimeout(closeTimer);
      } catch { /* best-effort explicit cancellation */ }
      const messages = useWorkbenchStore.getState().bySession[inflight.scratchSessionId] || [];
      const existing = messages.find((message) => message.id === inflight.messageId);
      useWorkbenchStore.getState().updateAssistant(inflight.messageId, {
        status: "error",
        text: existing?.text || "已停止生成。",
        progressText: "",
        error: "已由用户停止生成",
      }, inflight.scratchSessionId);
      clearInflightChatStream(inflight.streamId);
    }
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    if (msgWsRef.current) {
      try { msgWsRef.current.close(); } catch { /* noop */ }
      msgWsRef.current = null;
    }
    useWorkbenchStore.getState().setSending(false);
  };

  // Refresh/navigation detaches the browser transport only. The backend turn
  // remains alive and the next page instance resumes it from persisted cursor.
  useEffect(() => () => {
    detachedRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    try { msgWsRef.current?.close(); } catch { /* noop */ }
    msgWsRef.current = null;
    useWorkbenchStore.getState().setSending(false);
  }, []);

  const resumeInflightStream = useCallback(async (record: InflightChatStream): Promise<void> => {
    if (detachedRef.current || manualStopRef.current) return;
    const current = readInflightChatStream();
    if (!current || current.streamId !== record.streamId) return;

    let existing = useWorkbenchStore.getState().bySession[record.scratchSessionId]
      ?.find((message) => message.id === record.messageId);
    if (!existing) {
      const replacementId = useWorkbenchStore.getState().appendAssistantStreaming(record.scratchSessionId);
      record = { ...record, messageId: replacementId };
      writeInflightChatStream(record);
      existing = useWorkbenchStore.getState().bySession[record.scratchSessionId]
        ?.find((message) => message.id === replacementId);
    }
    if (existing?.status !== "streaming") {
      clearInflightChatStream(record.streamId);
      return;
    }

    useWorkbenchStore.getState().setSending(true);
    useWorkbenchStore.getState().updateAssistant(record.messageId, {
      progressText: "正在恢复运行连接…",
      progressElapsedMs: Math.max(0, Date.now() - Date.parse(record.startedAt)),
    }, record.scratchSessionId);

    let terminalPayload: Record<string, unknown> | null = null;
    let terminalError = "";
    let lastSeq = record.lastSeq;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/agent`;

    for (let attempt = 0; attempt < WS_RESUME_ATTEMPTS; attempt += 1) {
      const currentRecord = readInflightChatStream();
      if (
        detachedRef.current
        || manualStopRef.current
        || !currentRecord
        || currentRecord.streamId !== record.streamId
      ) return;
      const outcome = await new Promise<"terminal" | "retry" | "missing">((resolve) => {
        let finished = false;
        let socket: WebSocket;
        try {
          socket = new WebSocket(wsUrl);
        } catch {
          resolve("retry");
          return;
        }
        msgWsRef.current = socket;
        const finish = (value: "terminal" | "retry" | "missing") => {
          if (finished) return;
          finished = true;
          clearTimeout(openTimer);
          watchdog.stop();
          resolve(value);
        };
        const watchdog = createStreamActivityWatchdog({
          onTick: () => {
            if (detachedRef.current) return;
            useWorkbenchStore.getState().updateAssistant(record.messageId, {
              progressElapsedMs: Math.max(0, Date.now() - Date.parse(record.startedAt)),
            }, record.scratchSessionId);
          },
          onTimeout: () => {
            try { socket.close(); } catch { /* noop */ }
            finish("retry");
          },
        });
        const openTimer = setTimeout(() => {
          try { socket.close(); } catch { /* noop */ }
          finish("retry");
        }, WS_TIMEOUT_MS);

        socket.onopen = () => {
          clearTimeout(openTimer);
          socket.send(JSON.stringify({
            type: "resume",
            stream_id: record.streamId,
            after_seq: lastSeq,
            session_id: record.sessionId,
            workspace_id: record.workspaceId,
            auth_token: getApiAccessToken(),
          }));
        };
        socket.onmessage = (event) => {
          watchdog.touch();
          try {
            const msg = JSON.parse(event.data) as Record<string, unknown> & {
              type?: string; name?: string; message?: string; stream_seq?: number;
              data?: Record<string, unknown>;
            };
            if (typeof msg.stream_seq === "number" && msg.stream_seq > lastSeq) {
              lastSeq = msg.stream_seq;
              updateInflightChatStream(record.streamId, { lastSeq });
            }
            if (msg.type === "accepted") {
              useWorkbenchStore.getState().updateAssistant(record.messageId, {
                progressText: "连接已恢复，仍在处理…",
              }, record.scratchSessionId);
              return;
            }
            if (msg.type === "event") {
              const label = msg.name ? STAGE_LABELS[msg.name] : "";
              const elapsed = Number(msg.data?.elapsed_ms || 0);
              useWorkbenchStore.getState().updateAssistant(record.messageId, {
                progressText: label || "仍在处理…",
                progressElapsedMs: elapsed > 0
                  ? elapsed
                  : Math.max(0, Date.now() - Date.parse(record.startedAt)),
              }, record.scratchSessionId);
              return;
            }
            if (msg.type === "done") {
              terminalPayload = msg;
              finish("terminal");
              return;
            }
            if (msg.type === "error") {
              terminalError = String(msg.message || "实时运行失败");
              finish(terminalError === "stream_not_found" ? "missing" : "terminal");
            }
          } catch { /* malformed frames do not advance the cursor */ }
        };
        socket.onclose = () => finish("retry");
        socket.onerror = () => finish("retry");
      });

      try { msgWsRef.current?.close(); } catch { /* noop */ }
      msgWsRef.current = null;
      if (outcome === "terminal" || outcome === "missing") break;
      await new Promise((resolve) => setTimeout(resolve, Math.min(1000 * (2 ** attempt), 8000)));
    }

    if (detachedRef.current || manualStopRef.current) return;
    if (terminalPayload) {
      const payload = terminalPayload as Parameters<typeof agentResultFromWsDone>[0];
      const resolvedSid = String(payload.session_id || record.sessionId || "");
      if (record.scratchSessionId === "_scratch" && resolvedSid) {
        useWorkbenchStore.getState().moveSessionMessages("_scratch", resolvedSid);
        useSessionStore.getState().setCurrentSession(resolvedSid);
        useWorkbenchStore.getState().switchSession(resolvedSid);
        callbacksRef.current.onSessionResolved(resolvedSid);
      }
      const targetSid = resolvedSid || record.scratchSessionId;
      const result = agentResultFromWsDone(payload, String(payload.final_response || ""), resolvedSid);
      const toolCalls: InlineToolCall[] = (result.tool_calls || []).map((toolCall) => ({
        tool_id: toolCall.tool_id,
        tool_name: toolLabel(toolCall.tool_id),
        ok: toolCall.ok,
        summary: toolCall.summary,
        duration_ms: toolCall.duration_ms ?? undefined,
        errors: toolCall.errors,
        artifacts: toolCall.artifacts as InlineToolCall["artifacts"],
        orchestration: (toolCall.metadata?.orchestration || undefined) as InlineToolCall["orchestration"],
      }));
      useWorkbenchStore.getState().updateAssistant(record.messageId, {
        status: result.errors?.length ? "error" : "ready",
        text: sanitizeAssistantText(result.final_response || result.errors?.[0] || ""),
        result,
        toolCalls: toolCalls.length ? toolCalls : undefined,
        error: result.errors?.[0],
        trace_id: result.trace_id,
        run_id: result.turn_id,
        progressText: "",
      }, targetSid);
      useWorkbenchStore.getState().setLatestResult(result, targetSid);
      notifyRunCompleted();
      callbacksRef.current.onResult?.(result, record.scratchSessionId);
      if (resolvedSid) {
        sessionsApi.messages(resolvedSid, record.workspaceId)
          .then((response) => {
            if (response.messages?.length) useWorkbenchStore.getState().mergeFromBackend(resolvedSid, response.messages);
          })
          .catch(() => {});
      }
      clearInflightChatStream(record.streamId);
    } else {
      if (terminalError === "stream_not_found" && record.sessionId) {
        try {
          const response = await sessionsApi.messages(record.sessionId, record.workspaceId);
          if (response.messages?.length) {
            useWorkbenchStore.getState().mergeFromBackend(record.sessionId, response.messages);
            const recovered = useWorkbenchStore.getState().bySession[record.sessionId]
              ?.find((message) => message.id === record.messageId);
            if (recovered && recovered.status !== "streaming") {
              clearInflightChatStream(record.streamId);
              useWorkbenchStore.getState().setSending(false);
              notifyRunCompleted();
              return;
            }
          }
        } catch { /* fall through to an explicit unrecoverable state */ }
      }
      const message = terminalError === "stream_not_found"
        ? "原运行已不可恢复，可能是后端服务在执行期间重启。"
        : terminalError || "实时连接多次恢复失败，本轮运行状态未知。请检查运行记录后再决定是否重试。";
      useWorkbenchStore.getState().updateAssistant(record.messageId, {
        status: "error", text: message, error: message, progressText: "",
      }, record.scratchSessionId);
      clearInflightChatStream(record.streamId);
      callbacksRef.current.onInterruption?.(message);
    }
    useWorkbenchStore.getState().setSending(false);
  }, []);

  useEffect(() => {
    detachedRef.current = false;
    manualStopRef.current = false;
    const record = readInflightChatStream();
    const state = useWorkbenchStore.getState();
    for (const [sessionId, messages] of Object.entries(state.bySession)) {
      for (const message of messages) {
        if (message.status !== "streaming" || message.id === record?.messageId) continue;
        state.updateAssistant(message.id, {
          status: "error",
          text: message.text || "上次运行连接已中断。",
          error: "没有可恢复的运行连接",
          progressText: "",
        }, sessionId);
      }
    }
    if (!record || !workspaceId || record.workspaceId !== workspaceId) return;
    void resumeInflightStream(record);
  }, [workspaceId, resumeInflightStream]);

  const send = useCallback(async (args: {
    text: string;
    attachments: ChatStreamAttachment[];
    effectiveSessionId: string | null;
    turnMetadata?: Record<string, unknown>;
  }): Promise<void> => {
    detachedRef.current = false;
    manualStopRef.current = false;
    const { text, attachments, turnMetadata: metadataOverride = {} } = args;
    const ws = paramsRef.current;
    if (!ws.workspaceId) return;
    if (sending) return;

    const hasImages = attachments.some((a) => a.mime_type.startsWith("image/"));
    if (hasImages && ws.llmHealth.visionSupported === false) return;

    const effectiveSessionId = args.effectiveSessionId;
    // Reads that decide where a turn belongs must use the current render's
    // session snapshot, not a value captured by an earlier send callback.
    const activeSessionId = ws.sessionId;
    const scratch = effectiveSessionId ?? activeSessionId ?? "_scratch";

    // Append the user + streaming placeholder so the page can render immediately.
    const store = useWorkbenchStore.getState();
    store.appendUser(text, scratch, attachments.length ? attachments : undefined);
    const streamingMsgId = store.appendAssistantStreaming(scratch);
    const streamId = newChatStreamId();
    const inflightRecord: InflightChatStream = {
      streamId,
      workspaceId: ws.workspaceId,
      sessionId: effectiveSessionId || activeSessionId || "",
      scratchSessionId: scratch,
      messageId: streamingMsgId,
      startedAt: new Date().toISOString(),
      lastSeq: 0,
    };
    writeInflightChatStream(inflightRecord);
    useWorkbenchStore.getState().setSending(true);

    const fullText = text;
    const turnMetadata = { ...metadataOverride };

    // Try WebSocket streaming first, fall back to HTTP.
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsHost = window.location.host;
    const wsUrl = `${protocol}//${wsHost}/ws/agent`;
    abortRef.current = new AbortController();

    try {
      const socket = new WebSocket(wsUrl);
      msgWsRef.current = socket;

      // Token batching — buffer tokens, flush every 50ms.
      const tokenBufferRef = { pending: "" };
      const thinkFilter: { mode: ThinkFilterState } = { mode: "idle" };
      let streamState = beginModelStep();
      let streamedText = "";
      let resolvedSid: string = activeSessionId || "";

      const wsReady: Promise<void> = new Promise((resolve, reject) => {
        const timer = setTimeout(() => { reject(new Error("ws_timeout")); }, WS_TIMEOUT_MS);
        socket.onopen = () => { clearTimeout(timer); resolve(); };
        socket.onerror = () => { clearTimeout(timer); reject(new Error("ws_error")); };
      });
      await wsReady;

      socket.send(JSON.stringify({
        type: "message",
        stream_id: streamId,
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
        metadata?: Record<string, unknown>;
        errors?: string[];
        warnings?: string[];
        tool_decision?: AgentResult["tool_decision"];
        no_tool_reason?: string;
      } = {};
      let terminalFrameReceived = false;
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
            useWorkbenchStore.getState().updateAssistant(
              streamingMsgId, { progressElapsedMs: elapsedMs }, scratch,
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
            if (typeof msg.stream_seq === "number") {
              updateInflightChatStream(streamId, { lastSeq: msg.stream_seq });
            }
            switch (msg.type) {
              case "accepted":
                break;
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
                if (stageName === "model_started") {
                  streamState = beginModelStep(streamedText);
                  streamedText = "";
                  useWorkbenchStore.getState().updateAssistant(streamingMsgId, { text: "" }, scratch);
                }
                if (STAGE_LABELS[stageName]) {
                  const label = STAGE_LABELS[stageName];
                  const elapsedRaw = msg.data?.elapsed_ms;
                  const elapsedNum = typeof elapsedRaw === "number"
                    ? elapsedRaw
                    : parseInt(String(elapsedRaw || "0"), 10) || 0;
                  useWorkbenchStore.getState().updateAssistant(
                    streamingMsgId,
                    { progressText: label, progressElapsedMs: elapsedNum },
                    scratch,
                  );
                }
                if (stageName === "tool_call" || stageName === "tool_result") {
                  streamingResult.tool_calls_count = (streamingResult.tool_calls_count || 0) + 1;
                  const tid = msg.data?.tool_id || msg.data?.name || "";
                  if (tid) {
                    const storeState = useWorkbenchStore.getState();
                    const curr = storeState.bySession[scratch]?.find((m) => m.id === streamingMsgId);
                    const prevCalls = (curr?.toolCalls || []) as InlineToolCall[];
                    if (stageName === "tool_result") {
                      const ok = msg.data?.ok ?? msg.data?.status === "ok";
                      const nextCalls = prevCalls.map((t) =>
                        t.tool_id === tid ? { ...t, status: ok ? "done" : "fail", ok, summary: msg.data?.summary } : t,
                      );
                      storeState.updateAssistant(streamingMsgId, { toolCalls: nextCalls }, scratch);
                    } else if (!prevCalls.find((t) => t.tool_id === tid)) {
                      storeState.updateAssistant(streamingMsgId, {
                        toolCalls: [...prevCalls, { tool_id: tid, tool_name: toolLabel(tid), ok: false, status: "running" }],
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
                resolvedSid = msg.session_id || activeSessionId || "";
                streamedText = finalizeStreamText(streamState.draft, msg.final_response || "");
                streamingResult.session_id = msg.session_id;
                streamingResult.turn_id = msg.turn_id;
                streamingResult.trace_id = msg.trace_id;
                streamingResult.events = msg.events || streamingResult.events || [];
                streamingResult.tool_calls_count = msg.tool_calls_count || streamingResult.tool_calls_count;
                streamingResult.tool_calls = msg.tool_calls || [];
                streamingResult.metadata = msg.metadata || {};
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

      if (detachedRef.current) return;
      if (!terminalFrameReceived) {
        useWorkbenchStore.getState().updateAssistant(streamingMsgId, {
          progressText: interruptionReason ? "连接超时，正在恢复…" : "连接中断，正在恢复…",
        }, scratch);
        const latestRecord = readInflightChatStream();
        if (latestRecord?.streamId === streamId) await resumeInflightStream(latestRecord);
        return;
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
      }, resolvedSid);
      clearInflightChatStream(streamId);

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
      // WebSocket failed, fall back to HTTP.
      if (msgWsRef.current) { try { msgWsRef.current.close(); } catch { /* noop */ } }
      if (detachedRef.current) return;
      clearInflightChatStream(streamId);
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
          tool_id: tc.tool_id, tool_name: toolLabel(tc.tool_id), ok: tc.ok,
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
        const fallbackSid = activeSessionId ?? "_scratch";
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
      useWorkbenchStore.getState().setSending(false);
    }
  }, [sending, workspaceId, onSessionResolved, onResult, onInterruption, resumeInflightStream]);

  return { sending, send, stop: stopStream };
}
