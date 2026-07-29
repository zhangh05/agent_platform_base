import React, { useState, useRef, useEffect, useCallback, memo } from "react";
import { agentApi, knowledgeApi, memoryApi, sessionsApi, settingsApi, sseApi } from "../../api";
import { apiRequest, getApiAccessToken } from "../../api/client";
import { useSessionStore } from "../../stores/session";
import { useWorkbenchStore, type ChatMsg } from "../../stores/workbench";
import { useToastStore } from "../../stores/toast";
import { isApiError } from "../../types";
import type { AgentResult, ToolCallResult, InlineToolCall, SourceSummary } from "../../types";
import { sanitizeAssistantText, renderAssistantHtml, toolLabel, filterStreamingThink } from "../../utils/displayText";
import { beginModelStep, discardToolCallDraft, finalizeStreamText } from "../../utils/agentStream";
import { humanFailure } from "../../utils/humanizeError";
import "./WorkbenchHighlight";
import hljs from "highlight.js/lib/core";
import { agentResultFromWsDone } from "../../utils/wsResult";
import { notifyRunCompleted } from "../../utils/appEvents";
import { IconAlert, IconBolt, IconSend } from "../../components/Icon";
import { ApprovalBubble } from "../../components/ApprovalBubble";
import { RuntimeEventTimeline } from "../../components/RuntimeEventTimeline";
import "../../components/RuntimeEventTimeline.css";
import { formatFileSize } from "../../utils/format";
import { QUICK_CHIPS } from "./WorkbenchQuickChips";
import { TaskTrackingCard } from "../../components/TaskTrackingCard";

/* ── View mode ── */
type ViewMode = "chat" | "timeline";

interface WorkbenchAutoPrompt {
  prompt?: string;
  metadata?: Record<string, unknown>;
}

const EMPTY_CHAT_MESSAGES: ChatMsg[] = [];

/* ── timing constants ── */
// Auto-send delay for prompts pulled out of sessionStorage (e.g. workbench_auto_prompt)
// — short enough to feel responsive, long enough for the input frame to mount.
const AUTO_SEND_DELAY_MS = 500;
// Initial backoff for the system-WS reconnect loop; subsequent attempts grow
// exponentially up to WS_RECONNECT_MAX_MS.
const WS_RECONNECT_BASE_MS = 1000;
// Cap on the exponential reconnect delay.
const WS_RECONNECT_MAX_MS = 5000;
// Hard ceiling for the WS stream "ws_timeout" race (websocket_message vs.
// the server-side response). If the WS doesn't deliver within this window,
// the caller falls back to the HTTP path.
const WS_TIMEOUT_MS = 3000;
// Visual feedback duration for the "已复制" toast on code-copy clicks.
const COPY_FEEDBACK_MS = 2000;

/* ── safe storage wrappers ── */
function safeGetLocal(key: string): string | null {
  try { return typeof localStorage !== "undefined" ? localStorage.getItem(key) : null; } catch { return null; }
}
function safeSetLocal(key: string, val: string): void {
  try { if (typeof localStorage !== "undefined") localStorage.setItem(key, val); } catch { /* noop */ }
}
function safeRemoveLocal(key: string): void {
  try { if (typeof localStorage !== "undefined") localStorage.removeItem(key); } catch { /* noop */ }
}
function safeGetSession(key: string): string | null {
  try { return typeof sessionStorage !== "undefined" ? sessionStorage.getItem(key) : null; } catch { return null; }
}
function safeRemoveSession(key: string): void {
  try { if (typeof sessionStorage !== "undefined") sessionStorage.removeItem(key); } catch { /* noop */ }
}

/** Enhanced error classification lives in utils/humanizeError — re-exported
 *  under the legacy name so internal callsites stay short. */
const _humanFailure = humanFailure;

function retryStats(result?: AgentResult) {
  const summary = result?.metadata?.retry_summary || {};
  const events = result?.metadata?.retry_events || [];
  return {
    summary,
    events,
    attempts: Number(summary.retry_attempts || 0),
    succeeded: Number(summary.retry_succeeded || 0),
    failed: Number(summary.retry_failed || 0),
    blocked: Number(summary.retry_blocked || 0),
  };
}

function validationCorrectionStats(result?: AgentResult) {
  const summary = result?.metadata?.validation_correction_summary || {};
  return {
    attempts: Number(summary.attempts || 0),
    exhausted: Boolean(summary.exhausted),
  };
}

function retryBlockedLabel(reason?: string): string {
  const value = String(reason || "");
  if (value === "non_idempotent" || value === "execute_command_not_retryable" || value.includes("side_effect_not_retryable")) {
    return "未原样重放，避免重复副作用";
  }
  return `未自动重试：${value || "不满足安全重试条件"}`;
}

type TrackingSummary = NonNullable<AgentResult["metadata"]["tracking_summary"]>;
type TrackingEvent = NonNullable<AgentResult["metadata"]["tracking_events"]>[number];

function trackingStats(result?: AgentResult) {
  const summary: TrackingSummary = result?.metadata?.tracking_summary ?? ({} as TrackingSummary);
  const events: TrackingEvent[] = result?.metadata?.tracking_events ?? [];
  return {
    summary,
    events,
    taskId: String(summary.task_id || ""),
    status: String(summary.status || ""),
    done: Boolean(summary.done || summary.terminal),
    mode: String(summary.mode || ""),
    nextPollSeconds: Number(summary.next_poll_seconds || 0),
    suggestedNextAction: String(summary.suggested_next_action || ""),
    progress: summary.progress || ({} as Record<string, unknown>),
    taskSummary: summary.summary || ({} as Record<string, unknown>),
    stallRisk: Boolean(summary.stall_risk),
  };
}

// Stage label table mirrors core.runtime_engine/stage_events.py
// so we can translate backend events to friendly Chinese text.
const STAGE_LABELS: Record<string, string> = {
  turn_started:        "轮次开始",
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
  repair_attempt:      "重试节点",
  merge_completed:     "汇总执行结果",
  response_started:    "整理回复…",
  response_completed:  "回复已就绪",
  turn_completed:      "轮次完成",
  heartbeat:           "仍在处理…",
};


// ── Memoized message row — skips re-render when store updates unrelated messages ──
const MemoMessageRow = memo(function MemoMessageRow({ m, idx, total, renderFn }: {
  m: ChatMsg; idx: number; total: number;
  renderFn: (m: ChatMsg, idx: number, total: number) => React.ReactNode;
}) {
  return <>{renderFn(m, idx, total)}</>;
}, (prev, next) => {
  // Only re-render if THIS specific message's content changed
  return prev.m.text === next.m.text
    && prev.m.status === next.m.status
    && prev.m.toolCalls === next.m.toolCalls
    && prev.m.result === next.m.result
    && prev.m.progressText === next.m.progressText
    && prev.m.progressElapsedMs === next.m.progressElapsedMs
    && prev.idx === next.idx
    && prev.renderFn === next.renderFn;
});

export function TaskWorkbench() {
  const { currentWorkspaceId, currentSessionId } = useSessionStore();
  const sending = useWorkbenchStore((s) => s.sending);
  const lastUserInput = useWorkbenchStore((s) => s.lastUserInput);
  // Granular selector: only re-render when THIS session's messages change.
  // The fallback must be a stable reference (module-level EMPTY_CHAT_MESSAGES);
  // returning a fresh `[]` each call would fail Zustand's Object.is check and
  // produce "Maximum update depth exceeded".
  const visibleHistory = useWorkbenchStore(
    (s) => s.bySession?.[currentSessionId ?? "_scratch"] ?? EMPTY_CHAT_MESSAGES,
  );
  const appendUser = useWorkbenchStore((s) => s.appendUser);
  const appendAssistantStreaming = useWorkbenchStore((s) => s.appendAssistantStreaming);
  const updateAssistant = useWorkbenchStore((s) => s.updateAssistant);
  const setSending = useWorkbenchStore((s) => s.setSending);
  const switchSession = useWorkbenchStore((s) => s.switchSession);
  const moveSessionMessages = useWorkbenchStore((s) => s.moveSessionMessages);
  const mergeFromBackend = useWorkbenchStore((s) => s.mergeFromBackend);
  const setLatestResult = useWorkbenchStore((s) => s.setLatestResult);

  const [viewMode, setViewMode] = useState<ViewMode>("chat");
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<Array<{ id: string; name: string; size: string; file: File; uploading?: boolean }>>([]);

  // ── Scroll architecture (v4.1) ──
  // A plain scroll container is enough for the capped chat history and avoids
  // virtual-list measurement jumps while an assistant answer is streaming.
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const userScrolledUpRef = useRef(false);    // true = user intentionally scrolled up
  const atBottomRef = useRef(true);
  const sendingRef = useRef(false);

  const chatRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    sendingRef.current = sending;
  }, [sending]);

  const handleChatScroll = useCallback(() => {
    const el = chatRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
    atBottomRef.current = atBottom;
    setShowScrollBtn(!atBottom);
    if (!atBottom && !sendingRef.current) userScrolledUpRef.current = true;
    if (atBottom) userScrolledUpRef.current = false;
  }, []);

  const keepAtBottom = useCallback(() => {
    if (!userScrolledUpRef.current) {
      requestAnimationFrame(() => {
        const el = chatRef.current;
        if (!el) return;
        el.scrollTop = el.scrollHeight;
        atBottomRef.current = true;
        setShowScrollBtn(false);
      });
    }
  }, []);

  const handleScrollBtnClick = useCallback(() => {
    userScrolledUpRef.current = false;
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
    atBottomRef.current = true;
    setShowScrollBtn(false);
  }, []);

  const thinkFilter = useRef<{ mode: import("../../utils/displayText").ThinkFilterState }>({ mode: "idle" });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [llmHealth, setLlmHealth] = useState<{ connected: boolean; provider?: string; model?: string; recentFailure?: string }>({ connected: false });
  const toast = useToastStore((s) => s.show);
  const abortRef = useRef<AbortController | null>(null);
  // System and message streams use separate refs for system WebSocket and message WebSocket
  // to prevent race conditions where message streaming overwrites the
  // system WS reference and vice versa.
  const systemWsRef = useRef<WebSocket | null>(null);
  const msgWsRef = useRef<WebSocket | null>(null);
  const pendingAutoMetadataRef = useRef<Record<string, unknown> | null>(null);
  const onSendRef = useRef(onSend);
  useEffect(() => { onSendRef.current = onSend; }, [onSend]);

  // Stop generation: abort active request + close message WebSocket
  // Only close the message WebSocket; the persistent system stream stays alive.
  const stopGeneration = useCallback(() => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    if (msgWsRef.current) { try { msgWsRef.current.close(); } catch {} msgWsRef.current = null; }
    setSending(false);
  }, []);  // eslint-disable-line

  // Preserve current session id ref for cleanup
  const prevSessionId = useRef(currentSessionId);
  useEffect(() => { prevSessionId.current = currentSessionId; });

  // Clean up abort controller on unmount
  useEffect(() => () => { abortRef.current?.abort(); }, []);

  // LLM health — load once on mount
  useEffect(() => {
    settingsApi.llmStatus().then((s) => {
      if (!s) return;
      setLlmHealth({
        connected: s.connected, provider: s.provider || s.provider_type || "",
        model: s.model || "", recentFailure: s.recent_failure?.error_type ? s.recent_failure.error_summary : undefined,
      });
    }).catch(() => {});
  }, []);

  // ── Persistent system WebSocket — replaces all polling ──
  // Use systemWsRef for the persistent stream so message streaming cannot overwrite it.
  useEffect(() => {
    if (!currentWorkspaceId) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/agent`;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    let retryDelay = 1000; // start at 1s, exponential backoff capped at 30s

    const connect = () => {
      if (closed) return;
      let ws: WebSocket | null = null;
      try {
        ws = new WebSocket(wsUrl);
      } catch {
        // constructor can throw (e.g. invalid URL); schedule reconnect
        if (!closed) reconnectTimer = setTimeout(connect, WS_RECONNECT_MAX_MS);
        return;
      }
      systemWsRef.current = ws;
      ws.onopen = () => {
        retryDelay = WS_RECONNECT_BASE_MS; // reset on successful connection
        try {
          ws?.send(JSON.stringify({
            type: "ping",
            workspace_id: currentWorkspaceId,
            auth_token: getApiAccessToken(),
          }));
        } catch {}
      };
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "event") {
            window.dispatchEvent(new CustomEvent("ws-event", { detail: msg }));
          }
        } catch {}
      };
      ws.onclose = () => {
        systemWsRef.current = null;
        if (!closed) {
          reconnectTimer = setTimeout(connect, retryDelay);
          retryDelay = Math.min(retryDelay * 2, WS_RECONNECT_MAX_MS * 6);
        }
      };
      ws.onerror = () => {
        // Browser will fire onclose after this; don't force-close.
        // Just null the reference so onclose doesn't double-handle.
      };
    };

    connect();
    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try { systemWsRef.current?.close(); } catch {}
      systemWsRef.current = null;
    };
  }, [currentWorkspaceId]);

  // Input draft persistence: save to localStorage debounced, restore on mount
  const draftKey = `draft-${currentSessionId ?? "_scratch"}`;
  useEffect(() => {
    const saved = safeGetLocal(draftKey);
    if (saved) setInput(saved);
  }, [currentSessionId]);  // eslint-disable-line

  const handleInputChange = useCallback((val: string) => {
    setInput(val);
    safeSetLocal(draftKey, val);
  }, [draftKey]);

  // Clear draft after successful send
  const clearDraft = useCallback(() => {
    safeRemoveLocal(draftKey);
  }, [draftKey]);

  // Auto-grow input
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  }, [input]);

  // Pick up cross-page auto prompts.
  useEffect(() => {
    const autoRaw = safeGetSession("workbench_auto_prompt");
    if (autoRaw && currentWorkspaceId) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      let payload: WorkbenchAutoPrompt;
      try {
        payload = JSON.parse(autoRaw) as WorkbenchAutoPrompt;
      } catch {
        safeRemoveSession("workbench_auto_prompt");
        return;
      }
      const prompt = String(payload.prompt || "").trim();
      if (!prompt) {
        safeRemoveSession("workbench_auto_prompt");
        return;
      }
      pendingAutoMetadataRef.current = payload.metadata || {};
      setInput(prompt);
      safeRemoveSession("workbench_auto_prompt");
      timer = setTimeout(() => {
        void onSendRef.current(prompt, payload.metadata || {}, { appendUser: true });
      }, AUTO_SEND_DELAY_MS);
      return () => {
        if (timer) clearTimeout(timer);
      };
    }
  }, [currentWorkspaceId]); // do NOT include onSend — use ref to avoid re-render killing timeout

  // Session switch + sync
  useEffect(() => {
    switchSession(currentSessionId);
    if (!currentSessionId || !currentWorkspaceId) return;
    const ctrl = new AbortController();
    sessionsApi.messages(currentSessionId, currentWorkspaceId, ctrl.signal)
      .then((res) => { if (res.messages?.length) mergeFromBackend(currentSessionId, res.messages); })
      .catch(() => {});
    return () => ctrl.abort();
  }, [currentSessionId, currentWorkspaceId]);

  // SSE real-time timeline updates
  useEffect(() => {
    if (!currentSessionId || !currentWorkspaceId || typeof EventSource === "undefined") return;
    let closed = false;
    let es: EventSource | null = null;
    const refreshMessages = () => {
      sessionsApi.messages(currentSessionId, currentWorkspaceId)
        .then((res) => { if (res.messages?.length) mergeFromBackend(currentSessionId, res.messages); })
        .catch(() => {});
    };
    sessionsApi.get(currentSessionId, currentWorkspaceId)
      .then(() => {
        if (closed) return;
        es = sseApi.connect(currentSessionId, currentWorkspaceId);
        es.addEventListener("turn_completed", refreshMessages);
        es.onerror = () => { es?.close(); };
      })
      .catch(() => {});
    return () => {
      closed = true;
      if (es) {
        es.removeEventListener("turn_completed", refreshMessages);
        es.close();
      }
    };
  }, [currentSessionId, currentWorkspaceId]);

  async function onSend(
    textOverride?: string,
    metadataOverride?: Record<string, unknown>,
    options?: { appendUser?: boolean },
  ) {
    const hasAttachments = attachments.length > 0;
    const raw = typeof textOverride === "string" ? textOverride : input;
    const text = raw.trim();
    if ((!text && !hasAttachments) || sending) return;
    if (!currentWorkspaceId) {
      toast({ kind: "warning", title: "未选择工作区", body: "请在左侧选择一个工作区" });
      return;
    }

    setInput("");
    clearDraft();
    let fullText = text;
    const turnMetadata = metadataOverride || pendingAutoMetadataRef.current || {};
    pendingAutoMetadataRef.current = null;

    if (hasAttachments) {
      setAttachments((prev) => prev.map((a) => ({ ...a, uploading: true })));
      const results: string[] = [];
      const fileRefs: string[] = [];
      for (const a of attachments) {
        try {
          const form = new FormData();
          form.append("file", a.file);
          form.append("artifact_type", "general");
          form.append("title", a.name);
          form.append("workspace_id", currentWorkspaceId);
          const res = await apiRequest<{ ok: boolean; file: { file_id: string; path?: string; logical_type?: string }; artifact?: unknown; warnings?: string[] }>({
            method: "POST", url: `/workspaces/${currentWorkspaceId}/artifacts/upload`, data: form,
          });
          const fid = res.ok ? res.file?.file_id : "";
          if (fid) {
            results.push(a.name);
            fileRefs.push(`file_id=${fid}`);
          } else {
            results.push(`${a.name}(失败)`);
          }
        } catch { results.push(`${a.name}(失败)`); }
      }
      setAttachments([]);
      if (results.length > 0) {
        let uploadNote = `\n[已上传文件: ${results.join("、")}]`;
        if (fileRefs.length > 0) {
          uploadNote += `\n[文件路径: ${fileRefs.join("; ")}]`;
        }
        fullText = text ? text + uploadNote : uploadNote;
      }
    }

    const scratch = currentSessionId ?? "_scratch";
    if (options?.appendUser !== false) {
      appendUser(fullText, scratch);
    }
    const streamingMsgId = appendAssistantStreaming(scratch);
    userScrolledUpRef.current = false; // reset scroll state when sending a new message
    setSending(true);
    // Force initial scroll to bottom so user sees the streaming bubble appear
    requestAnimationFrame(() => keepAtBottom());

    // Try WebSocket streaming first, fall back to HTTP
    // Dev: proxied through Vite (port 5273). Prod: same-origin.
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsHost = window.location.host; // Includes port (5273 in dev, 8010 in prod)
    const wsUrl = `${protocol}//${wsHost}/ws/agent`;
    let ws: WebSocket | null = null;
    abortRef.current = new AbortController();

    try {
      ws = new WebSocket(wsUrl);
      // Use msgWsRef for one-off message streaming so it cannot interfere with the persistent system stream.
      msgWsRef.current = ws;

      // Track streaming state
      let streamedText = "";
      let streamState = beginModelStep();
      thinkFilter.current = { mode: "idle" };
      let resolvedSid: string = currentSessionId || "";

      // Token batching — buffer tokens, flush every 50ms instead
      // of one setState per token. Also pause persist during streaming;
      // we flush the final text on `done` and let persist run once.
      const TOKEN_FLUSH_MS = 50;
      const tokenBufferRef = { pending: "" };
      const wsReady: Promise<void> = new Promise((resolve, reject) => {
        const timer = setTimeout(() => { reject(new Error("ws_timeout")); }, WS_TIMEOUT_MS);
        ws!.onopen = () => { clearTimeout(timer); resolve(); };
        ws!.onerror = () => { clearTimeout(timer); reject(new Error("ws_error")); };
      });
      await wsReady;

      // Send message
      ws.send(JSON.stringify({
        type: "message",
        user_input: fullText,
        session_id: currentSessionId,
        workspace_id: currentWorkspaceId,
        metadata: turnMetadata,
        auth_token: getApiAccessToken(),
      }));

      // Receive streaming events
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

      await new Promise<void>((resolve) => {
        // Per-token setState is replaced with a 50ms flush so
        // we only re-render the streaming message ~20 times/sec instead
        // of ~63 times/sec (the provider's actual burst rate).
        const flushTokenBuffer = () => {
          if (!tokenBufferRef.pending) return;
          streamState.draft += tokenBufferRef.pending;
          streamedText = streamState.draft;
          tokenBufferRef.pending = "";
          useWorkbenchStore.getState().updateAssistant(
            streamingMsgId, { text: streamedText }, scratch,
          );
          keepAtBottom();
        };
        const flushTimer = setInterval(flushTokenBuffer, TOKEN_FLUSH_MS);

        // Set initial progress text on the assistant message so
        // the user sees "正在分析任务…" instead of an empty bubble.
        useWorkbenchStore.getState().updateAssistant(
          streamingMsgId, { progressText: "等待 SSOT Runtime 调度…" }, scratch,
        );

        ws!.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);
            switch (msg.type) {
              case "token":
                // Accumulate into buffer, not into the live state.
                // The 50ms timer (flushTokenBuffer) does the actual setState.
                const raw = msg.content || "";
                const visible = filterStreamingThink(raw, thinkFilter.current);
                tokenBufferRef.pending += visible;
                break;
              case "event":
                if (msg.data) {
                  streamingResult.events = [...(streamingResult.events || []), msg.data];
                }
                const stageName = msg.name as string;
                if (stageName === "model_started") {
                  streamState = beginModelStep(streamedText);
                  streamedText = "";
                  useWorkbenchStore.getState().updateAssistant(streamingMsgId, { text: "" }, scratch);
                }
                // Live SSOT Runtime stage label — replaces blank "思考中…"
                // with the actual current stage (planner / risk / exec / …)
                // plus an elapsed counter for heartbeats.
                if (STAGE_LABELS[stageName]) {
                  const label = STAGE_LABELS[stageName];
                  const elapsedRaw = msg.data?.elapsed_ms;
                  const elapsedNum = typeof elapsedRaw === "number"
                    ? elapsedRaw
                    : parseInt(String(elapsedRaw || "0"), 10) || 0;
                  useWorkbenchStore.getState().updateAssistant(
                    streamingMsgId,
                    {
                      progressText: label,
                      progressElapsedMs: elapsedNum,
                    },
                    scratch,
                  );
                }
                if (stageName === "tool_call" || stageName === "tool_result") {
                  streamingResult.tool_calls_count = (streamingResult.tool_calls_count || 0) + 1;
                  const tid = msg.data?.tool_id || msg.data?.name || "";
                  if (tid) {
                    // Update live tool calls directly on the streaming message
                    const store = useWorkbenchStore.getState();
                    const curr = store.bySession[scratch]?.find((m) => m.id === streamingMsgId);
                    const prevCalls = (curr?.toolCalls || []) as InlineToolCall[];
                    if (stageName === "tool_result") {
                      const ok = msg.data?.ok ?? msg.data?.status === "ok";
                      const nextCalls = prevCalls.map((t: InlineToolCall) =>
                        t.tool_id === tid ? { ...t, status: ok ? "done" : "fail", ok, summary: msg.data?.summary } : t
                      );
                      store.updateAssistant(streamingMsgId, { toolCalls: nextCalls }, scratch);
                    } else {
                      if (!prevCalls.find((t: InlineToolCall) => t.tool_id === tid)) {
                        store.updateAssistant(streamingMsgId, {
                          toolCalls: [...prevCalls, { tool_id: tid, tool_name: toolLabel(tid), ok: false, status: "running" }],
                        }, scratch);
                      }
                    }
                  }
                }
                if (stageName === "tool_call") {
                  // Flush pending tokens before discarding the draft.
                  flushTokenBuffer();
                  discardToolCallDraft(streamState);
                  streamedText = "";
                  useWorkbenchStore.getState().updateAssistant(streamingMsgId, { text: "" }, scratch);
                }
                // Keep scrolled to bottom after any event that changes content height
                keepAtBottom();
                break;
              case "done":
                // Flush any remaining buffered tokens before
                // the final text is computed.
                flushTokenBuffer();
                clearInterval(flushTimer);
                terminalFrameReceived = true;
                resolvedSid = msg.session_id || currentSessionId;
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
                // Clear the in-flight progress label since the
                // final assistant text replaces it.
                useWorkbenchStore.getState().updateAssistant(
                  streamingMsgId, { progressText: "" }, scratch,
                );
                resolve();
                break;
              case "error":
                clearInterval(flushTimer);
                terminalFrameReceived = true;
                // Flush whatever we had buffered, then keep
                // the partial text visible to the user.
                flushTokenBuffer();
                streamingResult.errors = [msg.message || msg.error || "Unknown error"];
                useWorkbenchStore.getState().updateAssistant(
                  streamingMsgId, { progressText: "" }, scratch,
                );
                resolve();
                break;
            }
          } catch { /* ignore parse errors */ }
        };

        // Flush buffered tokens on close — ensure token buffer is flushed and
        // streamedText is updated even if the WS closes before `done`.
        // This prevents partial text from being lost on abnormal close.
        ws!.onclose = () => {
          clearInterval(flushTimer);
          // Flush any remaining buffered tokens into the store.
          flushTokenBuffer();
          // If we haven't resolved yet (no `done` event received), update
          // the store with whatever text we have so far.
          if (tokenBufferRef.pending || streamState.draft !== streamedText) {
            streamState.draft += tokenBufferRef.pending;
            streamedText = streamState.draft;
            tokenBufferRef.pending = "";
            useWorkbenchStore.getState().updateAssistant(
              streamingMsgId, { text: streamedText }, scratch,
            );
          }
          resolve();
        };
        ws!.onerror = () => {
          clearInterval(flushTimer);
          // Flush buffered tokens on error path.
          flushTokenBuffer();
          if (tokenBufferRef.pending || streamState.draft !== streamedText) {
            streamState.draft += tokenBufferRef.pending;
            streamedText = streamState.draft;
            tokenBufferRef.pending = "";
            useWorkbenchStore.getState().updateAssistant(
              streamingMsgId, { text: streamedText }, scratch,
            );
          }
          resolve();
        };
      });

      if (!terminalFrameReceived) {
        const interruption = "实时连接已中断，未收到本轮完成消息。请重试。";
        streamingResult.errors = [interruption];
        if (!streamedText.trim()) streamedText = interruption;
      } else if (streamingResult.errors?.length && !streamedText.trim()) {
        streamedText = streamingResult.errors[0];
      }

      try { ws.close(); } catch { /* already closed */ }
      ws = null;
      // Clear only the message WebSocket ref after the turn completes.
      msgWsRef.current = null;

      // Resolve new-session routing before writing the final assistant text.
      // The streaming placeholder starts in "_scratch"; if we write the final
      // answer to the backend session before moving it, updateAssistant becomes
      // a no-op and the user only sees the answer after a manual refresh.
      if (!currentSessionId && resolvedSid) {
        moveSessionMessages("_scratch", resolvedSid);
        useSessionStore.getState().setCurrentSession(resolvedSid);
        switchSession(resolvedSid);
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
      }));
      updateAssistant(streamingMsgId, {
        status: wsResult.errors?.length ? "error" : "ready",
        text: cleanText,
        result: cleanResult,
        toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
        error: wsResult.errors?.[0],
        trace_id: wsResult.trace_id,
        run_id: wsResult.turn_id,
      }, resolvedSid);
      // Defer heavy post-processing
      queueMicrotask(() => {
        setLatestResult(wsResult, resolvedSid);
        notifyRunCompleted();
        keepAtBottom();
      });

      if (resolvedSid && currentWorkspaceId) {
        sessionsApi.messages(resolvedSid, currentWorkspaceId)
          .then((r) => { if (r.messages?.length) mergeFromBackend(resolvedSid, r.messages); })
          .catch(() => {});
      }

    } catch {
      // WebSocket failed, fall back to HTTP
      if (ws) { try { ws.close(); } catch {} }
      try {
        const res = await agentApi.run({
          message: fullText,
          workspace_id: currentWorkspaceId,
          session_id: currentSessionId,
          metadata: turnMetadata,
        });
        const resolvedSid = (res.session_id && res.session_id !== "—" ? res.session_id : currentSessionId) ?? undefined;
        if (!currentSessionId && resolvedSid) {
          moveSessionMessages("_scratch", resolvedSid);
          useSessionStore.getState().setCurrentSession(resolvedSid);
          switchSession(resolvedSid);
        }
        const tcArray = (res.tool_calls ?? []).map((tc: ToolCallResult) => ({
          tool_id: tc.tool_id, tool_name: toolLabel(tc.tool_id), ok: tc.ok,
          summary: tc.summary, duration_ms: tc.duration_ms ?? undefined,
          errors: tc.errors, artifacts: tc.artifacts,
        }));
        updateAssistant(streamingMsgId, {
          status: res.ok ? "ready" : "error",
          text: sanitizeAssistantText(res.final_response ?? ""),
          result: res,
          toolCalls: tcArray.length > 0 ? tcArray : undefined,
          error: !res.ok ? res.errors?.[0] : undefined,
          trace_id: res.trace_id,
          run_id: res.turn_id,
        }, resolvedSid);
        setLatestResult(res, resolvedSid);
        notifyRunCompleted();
        keepAtBottom();
        if (res.ok) {
          toast({ kind: "success", title: "回答完成", body: "可切换到时间线视图查看执行详情" });
        } else {
          toast({ kind: "error", title: "请求失败", body: _humanFailure(res.error_type ?? "", res.errors?.[0] ?? "").msg });
        }
        if (resolvedSid && currentWorkspaceId) {
          sessionsApi.messages(resolvedSid, currentWorkspaceId)
            .then((r) => { if (r.messages?.length) mergeFromBackend(resolvedSid, r.messages); })
            .catch(() => { /* 背景同步为 best-effort，静默失败 */ });
        }
      } catch (err: unknown) {
        const msg = isApiError(err) ? err.message : String(err);
        const fallbackSid = currentSessionId ?? "_scratch";
        const stubResult: AgentResult = {
          ok: false, final_response: sanitizeAssistantText(`(error) ${msg}`),
          events: [], trace_id: isApiError(err) ? err.request_id ?? "—" : "—",
          session_id: fallbackSid ?? "—", turn_id: `turn-${Date.now()}`,
          tool_calls: [], warnings: [], errors: [msg], error_type: "network",
          metadata: { source_count: 0, source_summary: [] },
        };
        updateAssistant(streamingMsgId, {
          status: "error",
          text: stubResult.final_response,
          result: stubResult,
          error: msg,
        }, fallbackSid);
        setLatestResult(stubResult, fallbackSid);
        toast({ kind: "error", title: "请求失败", body: msg });
      }
    } finally {
      msgWsRef.current = null;
      setSending(false);
    }
  }

  function pickChip(prompt: string) {
    setInput(prompt);
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  // ── File upload ──

  function addFiles(files: FileList | File[]) {
    const list = Array.from(files).filter((f) => f.size < 50 * 1024 * 1024);
    if (list.length < files.length) toast({ kind: "warning", title: "部分文件跳过", body: "单文件不能超过 50 MB" });
    setAttachments((prev) => [
      ...prev,
      ...list.map((f) => ({ id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, name: f.name, size: formatFileSize(f.size), file: f })),
    ]);
  }

  function removeAttachment(id: string) {
    setAttachments((prev) => prev.filter((a) => a.id !== id));
  }

  function pickFile() {
    fileInputRef.current?.click();
  }

  // Drag-drop handler
  const handleDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); }, []);
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  }, []);

  // Paste handler — capture images from clipboard
  useEffect(() => {
    const handler = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const files: File[] = [];
      for (let i = 0; i < items.length; i++) {
        const f = items[i].getAsFile();
        if (f && f.type.startsWith("image/")) files.push(f);
      }
      if (files.length) addFiles(files);
    };
    window.addEventListener("paste", handler);
    return () => window.removeEventListener("paste", handler);
  }, []);

  const llmStatusLabel = llmHealth.connected
    ? llmHealth.recentFailure ? "LLM 可用 · 最近一次请求超时，可重试" : `LLM 可用 · ${llmHealth.model || llmHealth.provider || "在线"}`
    : "LLM 离线";

  useEffect(() => {
    keepAtBottom();
  }, [
    keepAtBottom,
    sending,
    visibleHistory.length,
    visibleHistory[visibleHistory.length - 1]?.text,
    visibleHistory[visibleHistory.length - 1]?.status,
  ]);

  // Message row renderer for the chat list
  const renderMsg = useCallback((m: ChatMsg, _idx: number, _total: number) => {
    if (m.role === "user") {
      return (
        <div className="message-row user" data-testid="chat-user">
          <div className="message-stack"><div className="chat-bubble user">{m.text}</div></div>
          <div className="message-avatar user">我</div>
        </div>
      );
    }
    return (
      <div className={`message-row assistant${m.status === "error" ? " error" : ""}${m.status === "streaming" ? " streaming" : ""}`} data-testid="chat-assistant">
        <div className="message-avatar agent">网</div>
        <div className="message-stack">
          {/* Live tool call chips during streaming */}
          {m.status === "streaming" && m.toolCalls && m.toolCalls.length > 0 && (
            <div className="tool-calls-inline">
              {m.toolCalls.map((tc: InlineToolCall, tci: number) => (
                <span key={`${tc.tool_id}-${tci}`} className={`live-tool-chip ${tc.status || "running"}`}>
                  <span className={`live-tool-dot ${tc.status || "running"}`} />
                  {tc.tool_name || toolLabel(tc.tool_id)}
                  {tc.summary && <span className="live-tool-summary">{tc.summary.slice(0, 40)}</span>}
                </span>
              ))}
            </div>
          )}
          {/* Completed tool call cards */}
          {m.status !== "streaming" && m.toolCalls && m.toolCalls.length > 0 && (
            <div className="tool-calls-inline">
              {m.toolCalls.map((tc: InlineToolCall, tci: number) => (
                <InlineToolCallCard key={`${tc.tool_id}-${tci}`} toolCall={tc} seq={tci + 1} />
              ))}
            </div>
          )}
          {m.status === "streaming" ? (
            <div className="chat-bubble assistant sending-line">
              {/* Live progress label — replaces the static
                  "思考中…" so the user sees which SSOT Runtime stage is running
                  (planner / risk / exec / response). Empty when not
                  streaming or before the first event arrives. */}
              {m.progressText && (
                <div className="ssot-runtime-progress-row" data-testid="ssot-runtime-progress">
                  <span className="typing-indicator">
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                  </span>
                  <span className="text-sm wb-progress-text">
                    {m.progressText}
                    {m.progressElapsedMs != null && m.progressElapsedMs > 0 ? (
                      <span className="muted wb-progress-elapsed">
                        ({m.progressElapsedMs >= 1000
                          ? `${(m.progressElapsedMs / 1000).toFixed(1)}s`
                          : `${m.progressElapsedMs}ms`})
                      </span>
                    ) : null}
                  </span>
                </div>
              )}
              {m.text ? (
                <StreamingContent text={m.text} />
              ) : !m.progressText ? (
                <div className="wb-thinking-row">
                  <span className="typing-indicator"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></span>
                  <span className="text-sm muted wb-thinking-label">思考中…</span>
                </div>
              ) : null}
            </div>
          ) : (
            <>
              {(() => {
                const { thinking, body } = parseThinking(m.text);
                const html = body ? renderAssistantHtml(body) : "";
                return (<>
                  {thinking && <ThinkingBlock content={thinking} />}
                  {html ? (
                    <div className="chat-bubble assistant markdown-body" onClick={handleCodeCopyClick} dangerouslySetInnerHTML={{ __html: highlightCode(html) }} />
                  ) : (!m.text) ? (
                    <span className="muted">(空消息)</span>
                  ) : null}
                </>);
              })()}
              <ResultInline
                result={m.result}
                fallbackText={sanitizeAssistantText(m.text)}
                onRetryOriginal={lastUserInput ? () => onSendRef.current(lastUserInput) : undefined}
              />
            </>
          )}
          {m.status === "error" && m.error && (
            <div className="msg-error-box">
              <span>⚠️ {_humanFailure(m.result?.error_type, m.error ?? "").msg}</span>
            </div>
          )}
        </div>
      </div>
    );
  }, [handleCodeCopyClick]);

  return (
    <div className="wb-shell">
      {/* ── Header bar ── */}
      <div className="wb-header">
        <div className="wb-header-status">
          <span className={"dot " + (llmHealth.connected ? (llmHealth.recentFailure ? "warn" : "ok") : "err")} />
          <span>{llmStatusLabel}</span>
        </div>
        {/* Export session as Markdown */}
        {currentSessionId && visibleHistory && visibleHistory.length > 0 && (
          <button className="wb-export-btn" title="导出对话" onClick={() => {
            const md = visibleHistory.map((m) =>
              `## ${m.role === "user" ? "🙋 用户" : "🤖 AI"}\n\n${m.text}\n\n---\n`
            ).join("\n");
            const blob = new Blob([md], { type: "text/markdown" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `session-${currentSessionId.slice(0, 8)}-${new Date().toISOString().slice(0, 10)}.md`;
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 100);
          }}>📥 导出</button>
        )}
      </div>

      {/* ── View mode toggle ── */}
      <div className="wb-view-tabs">
        <button
          type="button"
          className={`wb-view-tab ${viewMode === "chat" ? "active" : ""}`}
          onClick={() => setViewMode("chat")}
          data-testid="view-chat"
        >
          💬 对话
        </button>
        <button
          type="button"
          className={`wb-view-tab ${viewMode === "timeline" ? "active" : ""}`}
          onClick={() => setViewMode("timeline")}
          data-testid="view-timeline"
        >
          📋 时间线
        </button>
      </div>

      {/* ── Content area ── */}
      <div className="wb-chat" data-testid="chat-stream">
        {viewMode === "timeline" ? (
          <RuntimeEventTimeline messages={visibleHistory ?? []} />
        ) : (visibleHistory?.length ?? 0) === 0 && !sending ? (
          <div className="wb-empty" data-testid="workbench-empty">
            <h2>任务工作台</h2>
            <p>输入故障现象、配置片段或排查目标，AI Agent 按事件时间线组织执行过程。</p>
            <div className="wb-empty-chips">
              {QUICK_CHIPS.map((c) => (
                <button key={c.label} className="wb-input-chip" type="button" onClick={() => pickChip(c.prompt)} title={c.prompt}>
                  {c.label}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div
            ref={chatRef}
            className="wb-chat-list"
            role="log"
            aria-live={sending ? "polite" : "off"}
            onScroll={handleChatScroll}
          >
            {(visibleHistory ?? []).map((m, idx) => (
              <MemoMessageRow key={m.message_id || m.id} m={m} idx={idx} total={(visibleHistory ?? []).length} renderFn={renderMsg} />
            ))}
          </div>
        )}

        {/* ── Scroll-to-bottom floating bubble ── */}
        {showScrollBtn && (
          <button className="scroll-bottom-btn" onClick={handleScrollBtnClick} title="回到底部" type="button">
            <svg width="14" height="14" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </button>
        )}
      </div>

      {/* ── Retry bar (derive from last assistant message's result) ── */}
      {(() => {
        if (sending || !lastUserInput) return null;
        const lastAssistant = [...(visibleHistory ?? [])].reverse().find((m) => m.role === "assistant");
        const lastResult = lastAssistant?.result;
        if (!lastResult) return null;
        if (lastResult.ok) return null;
        return (
          <div className="wb-retry-bar">
            <IconAlert size={11} />
            <span>{_humanFailure(lastResult.error_type, lastResult.errors?.[0] ?? "请求失败").msg}</span>
            {_humanFailure(lastResult.error_type, lastResult.errors?.[0] ?? "").retryable && (
              <button type="button" onClick={() => onSendRef.current(lastUserInput)} data-testid="retry-btn">
                🔄 重试
              </button>
            )}
          </div>
        );
      })()}

      {/* ── Input bar ── */}
      <div className="wb-input-bar" onDragOver={handleDragOver} onDrop={handleDrop}>
        {attachments.length > 0 && (
          <div className="wb-attachments">
            {attachments.map((a) => (
              <span key={a.id} className="tag wb-attachment-tag">
                {a.uploading ? <span className="spinner wb-attachment-spinner" /> : "📄"}
                <span className="wb-attachment-name">{a.name}</span>
                <button onClick={() => removeAttachment(a.id)} className="wb-attachment-remove" type="button">&times;</button>
              </span>
            ))}
          </div>
        )}
        <div className="wb-input-row">
            <input ref={fileInputRef} type="file" multiple accept=".txt,.pdf,.md,.json,.csv,.log,.conf,.cfg,.yaml,.yml,.png,.jpg,.jpeg,.gif,.webp" onChange={(e) => { if (e.target.files) { addFiles(e.target.files); e.target.value = ""; } }} className="wb-file-input" />
            <button className="wb-attach-btn" onClick={pickFile} disabled={sending} title="上传文件 (Ctrl+V 粘贴图片 / 拖拽)" type="button">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M8.5 1.5v9M5 5l3.5-3.5L12 5M2.5 10v2.5a1 1 0 001 1h9a1 1 0 001-1V10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </button>
            <textarea
              ref={inputRef}
              className="wb-input wb-input-content"
              placeholder="输入主机名、IP 或排查目标… (Enter 发送, Shift+Enter 换行)"
              value={input}
              onChange={(e) => handleInputChange(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); onSend(); } }}
              disabled={sending}
              rows={1}
              data-testid="chat-input"
              spellCheck={false}
            />
            {sending ? (
              <button className="wb-stop" onClick={stopGeneration} title="停止生成" type="button" data-testid="btn-stop">
                <svg width="12" height="12" viewBox="0 0 12 12"><rect x="1" y="1" width="10" height="10" rx="2" fill="currentColor"/></svg>
              </button>
            ) : (
              <button
                className="wb-send"
                onClick={() => onSend()}
                disabled={!input.trim() && attachments.length === 0}
                data-testid="btn-send"
                type="button"
                aria-label="发送"
                title="Enter 发送"
              >
                <IconSend size={14} />
              </button>
            )}
          </div>
      </div>

      {/* ── Inline approval bubble for high-risk tools ── */}
      <ApprovalBubble />
    </div>
  );
}

/* ==============================================================
   Inline tool call card
   ============================================================== */

function InlineToolCallCard({ toolCall, seq }: { toolCall: InlineToolCall; seq: number }) {
  const [open, setOpen] = useState(false);
  const errText = toolCall.errors?.join(", ");
  return (
    <div className={`tool-call-card ${toolCall.ok ? "ok" : "fail"}`} onClick={() => setOpen(!open)}>
      <div className="tool-call-card-header">
        <span className="tc-seq">#{seq}</span>
        <span className="tc-icon">{toolCall.ok ? "✅" : "❌"}</span>
        <span className="tc-name">{toolCall.tool_name}</span>
        <span className="tc-chev">{open ? "▾" : "▸"}</span>
      </div>
      {open && (
        <div className="tool-call-card-body">
          {toolCall.summary && <div className="tc-summary">{toolCall.summary}</div>}
          {errText && <div className="tc-error">{errText}</div>}
          {toolCall.duration_ms != null && (
            <div className="tc-duration">{(toolCall.duration_ms / 1000).toFixed(1)}s</div>
          )}
          {toolCall.artifacts && toolCall.artifacts.length > 0 && (
            <div className="tc-artifacts">
              {toolCall.artifacts.map((a) => (
                <span key={a.artifact_id} className="tc-artifact-tag">📄 {a.title || a.artifact_id}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ==============================================================
   Helpers
   ============================================================== */

/** Parse <think>...</think> and <thinking>...</thinking> blocks from content */
function parseThinking(text: string): { thinking: string; body: string } {
  const pat = /<(?:think|thinking)>([\s\S]*?)<\/(?:think|thinking)>/i;
  const match = text.match(pat);
  if (match) {
    return { thinking: match[1].trim(), body: text.replace(match[0], "").trim() };
  }
  return { thinking: "", body: text };
}

/** Highlight code blocks in rendered HTML.
 *  Cached by (lang + decoded source) so identical code blocks highlight only
 *  once — avoids blocking the main thread when a session repeats the same
 *  output (e.g. the same error block across many messages). */
const highlightCache = new Map<string, string>();
const HL_CACHE_MAX = 2000;
function highlightCode(html: string): string {
  return html.replace(/<pre><code class="language-([^"]+)">([\s\S]*?)<\/code><\/pre>/g, (_, lang, code) => {
    try {
      const decoded = new DOMParser().parseFromString(code, "text/html").body.textContent || "";
      const cacheKey = (lang || "") + " " + decoded;
      const hit = highlightCache.get(cacheKey);
      if (hit !== undefined) return hit;
      const langClass = lang && hljs.getLanguage(lang) ? lang : "plaintext";
      const result = hljs.highlight(decoded, { language: langClass }).value;
      const wrapped = `<div class="code-block-wrap"><div class="code-block-header"><span>${lang || "code"}</span><button class="code-copy-btn" type="button" data-code-copy="1">复制</button></div><pre><code class="hljs language-${langClass}">${result}</code></pre></div>`;
      if (highlightCache.size >= HL_CACHE_MAX) highlightCache.clear();
      highlightCache.set(cacheKey, wrapped);
      return wrapped;
    } catch {
      return `<pre><code>${code}</code></pre>`;
    }
  });
}

function handleCodeCopyClick(event: React.MouseEvent<HTMLDivElement>) {
  const target = event.target as HTMLElement | null;
  const button = target?.closest("[data-code-copy]") as HTMLButtonElement | null;
  if (!button) return;
  const code = button.closest(".code-block-wrap")?.querySelector("code")?.textContent || "";
  void navigator.clipboard?.writeText(code);
  button.textContent = "已复制";
  window.setTimeout(() => {
    button.textContent = "复制";
  }, COPY_FEEDBACK_MS);
}

/** Streaming content with live thinking block support */
function StreamingContent({ text }: { text: string }) {
  const { thinking, body } = parseThinking(text);
  return (
    <>
      {thinking && <ThinkingBlock content={thinking} defaultOpen />}
      {body && <span className="text-sm">{body}</span>}
      {!body && !thinking && <span className="text-sm">{text}</span>}
    </>
  );
}

/** Collapsible thinking/reasoning block */
function ThinkingBlock({ content, defaultOpen }: { content: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="thinking-block">
      <div className={`thinking-header ${open ? "open" : ""}`} onClick={() => setOpen(!open)}>
        <span className="chev">▸</span>
        <span>💭 思考过程</span>
        <span className="thinking-header-toggle">点击{open ? "收起" : "展开"}</span>
      </div>
      {open && <div className="thinking-body">{content}</div>}
    </div>
  );
}

/* ==============================================================
   Sub-components
   ============================================================== */

const ResultInline = React.memo(function ResultInline({
  result,
  fallbackText,
  onRetryOriginal,
  onRetryAlternative,
}: {
  result: AgentResult | undefined;
  fallbackText: string;
  onRetryOriginal?: () => void;
  onRetryAlternative?: () => void;
}) {
  const { currentWorkspaceId } = useSessionStore();
  const toast = useToastStore((s) => s.show);
  const [saving, setSaving] = useState<"" | "memory" | "knowledge">("");
  const summaries: SourceSummary[] = (result?.metadata?.context_sources ?? result?.metadata?.source_summary ?? []);
  const isFailed = !result?.ok;
  const hasFailedTool = ((result?.tool_calls) ?? []).some((tc) => !tc.ok);
  const finalText = (result?.final_response || fallbackText || "").trim();
  const retry = retryStats(result);
  const validationCorrection = validationCorrectionStats(result);
  const toolRecoveryEvents = result?.metadata?.tool_recovery_events || [];
  const tracking = trackingStats(result);
  const toolCalls = result?.tool_calls ?? [];
  const actionCount = toolCalls.length;
  const failedToolCount = toolCalls.filter((tc) => !tc.ok).length;
  const successToolCount = toolCalls.filter((tc) => tc.ok).length;
  const contextCompacted = Boolean(result?.metadata?.context_compacted);
  const outputTruncated = Boolean(result?.metadata?.output_truncated);
  const truncationReason = String(result?.metadata?.output_truncation_reason || "");
  const showActionTrace = !!result && (actionCount > 0 || retry.events.length > 0 || validationCorrection.attempts > 0 || toolRecoveryEvents.length > 0 || tracking.taskId || isFailed);

  // Nothing to show — no result and no fallback text
  if (!result && !fallbackText) return null;

  async function rememberAnswer() {
    if (!finalText) { toast({ kind: "warning", title: "无法保存", body: "当前回答内容为空" }); return; }
    if (!currentWorkspaceId) { toast({ kind: "warning", title: "未选择工作区", body: "请先在左侧选择工作区" }); return; }
    if (saving) return;
    setSaving("memory");
    try {
      const res = await memoryApi.create({
        workspace_id: currentWorkspaceId,
        title: finalText.slice(0, 42) || "本次结论",
        content: finalText,
        memory_type: "knowledge_note",
        tags: ["agent_answer", "confirmed"],
        user_confirmed: true,
      });
      // Also save to unified files for File Manager visibility
      try {
        const file = new File([finalText], `${finalText.slice(0, 30)}.txt`, { type: "text/plain" });
        const form = new FormData();
        form.append("file", file);
        form.append("artifact_type", "memory");
        form.append("title", finalText.slice(0, 42) || "本次结论");
        form.append("workspace_id", currentWorkspaceId);
        await apiRequest({ method: "POST", url: `/workspaces/${currentWorkspaceId}/artifacts/upload`, data: form });
      } catch {}
      if (res.conflict) {
        toast({ kind: "warning", title: "已记录，但发现冲突", body: "这条记忆和已有记忆可能不一致，请稍后在记忆列表核对。" });
      } else {
        toast({ kind: "success", title: "已记住", body: "后续对话会通过 RAG 召回这条结论" });
      }
    } catch (e: unknown) {
      toast({ kind: "error", title: "记忆失败", body: isApiError(e) ? e.message : String(e) });
    } finally {
      setSaving("");
    }
  }

  async function saveAsKnowledge() {
    if (!finalText) { toast({ kind: "warning", title: "无法保存", body: "当前回答内容为空" }); return; }
    if (!currentWorkspaceId) { toast({ kind: "warning", title: "未选择工作区", body: "请先在左侧选择工作区" }); return; }
    if (saving) return;
    setSaving("knowledge");
    try {
      const title = `对话结论-${new Date().toISOString().slice(0, 10)}`;
      const body = `# ${title}\n\n${finalText}\n`;
      const file = new File([body], `${title}.md`, { type: "text/markdown" });
      await knowledgeApi.upload(currentWorkspaceId, file, {
        title,
        tags: "agent_answer,chat",
        source_type: "project_doc",
        scope: "workspace",
        language: "zh",
      });
      // Also save to unified files for File Manager visibility
      try {
        const form = new FormData();
        form.append("file", file);
        form.append("artifact_type", "knowledge");
        form.append("title", title);
        form.append("workspace_id", currentWorkspaceId);
        await apiRequest({ method: "POST", url: `/workspaces/${currentWorkspaceId}/artifacts/upload`, data: form });
      } catch {}
      toast({ kind: "success", title: "已保存到知识库", body: "这条回答已整理为可检索文档" });
    } catch (e: unknown) {
      toast({ kind: "error", title: "保存失败", body: isApiError(e) ? e.message : String(e) });
    } finally {
      setSaving("");
    }
  }

  return (
    <div className="chat-result-inline">
      {(contextCompacted || outputTruncated) && (
        <div
          className={`context-budget-notice ${outputTruncated ? "warning" : ""}`}
          data-testid="context-budget-notice"
        >
          {outputTruncated
            ? truncationReason === "timeout"
              ? "模型响应超时，当前展示的是已接收内容。"
              : "回复达到输出长度上限，当前内容可能不完整。"
            : "较早的运行上下文已压缩，最近对话和关键任务引用仍被保留。"}
        </div>
      )}
      {((result?.tool_calls) ?? []).length > 0 && (
        <div className="chat-tool-summary" data-testid="inline-tool-summary">
          <IconBolt size={10} className="inline-icon-accent" />
          <span>{toolCallSummary(result?.tool_calls ?? [])}</span>
          <details className="inline-technical-details">
            <summary>技术详情</summary>
            <div className="chat-tool-calls">
              {(result?.tool_calls ?? []).map((tc: ToolCallResult, idx: number) => (
                <span key={tc.call_id || `${tc.tool_id}-${idx}`} className="chat-tool-call">
                  <span className="tc-name">{toolLabel(tc.tool_id)}</span>
                  <span className={"tc-status " + (tc.ok ? "ok" : "err")}>
                    {tc.ok ? "已完成" : "需关注"}
                  </span>
                </span>
              ))}
            </div>
          </details>
        </div>
      )}

      {showActionTrace && (
        <div className="action-trace-panel" data-testid="action-trace-panel">
          <div className="action-trace-head">
            <span className="action-trace-title">动作跟踪</span>
            <span className="action-trace-pill">{actionCount} 个工具</span>
            <span className="action-trace-pill ok">{successToolCount} 成功</span>
            {failedToolCount > 0 && <span className="action-trace-pill danger">{failedToolCount} 需关注</span>}
            {retry.attempts > 0 && <span className="action-trace-pill warn">{retry.attempts} 次自动重试</span>}
            {validationCorrection.attempts > 0 && (
              <span className={`action-trace-pill ${validationCorrection.exhausted ? "danger" : "ok"}`}>
                {validationCorrection.attempts} 次参数自纠
              </span>
            )}
            {toolRecoveryEvents.length > 0 && <span className="action-trace-pill ok">{toolRecoveryEvents.length} 次改策略继续</span>}
            {retry.blocked > 0 && <span className="action-trace-pill muted">{retry.blocked} 次未重试</span>}
          </div>
          {retry.events.length > 0 ? (
            <div className="action-retry-list">
              {retry.events.slice(0, 4).map((ev, i) => (
                <div className="action-retry-row" key={`${ev.node_id || ev.tool_id || "retry"}-${i}`}>
                  <span className={`action-retry-dot ${ev.retry_allowed ? (ev.final_status === "succeeded" ? "ok" : "warn") : "muted"}`} />
                  <span className="action-retry-main">
                    <b>{toolLabel(String(ev.tool_id || ev.node_id || "工具"))}</b>
                    {ev.retry_allowed
                      ? ev.final_status === "succeeded"
                        ? " 首次失败后已恢复"
                        : " 已重试但仍失败"
                      : ` ${retryBlockedLabel(ev.reason)}`}
                  </span>
                  {ev.backoff_ms ? <span className="action-retry-meta">{ev.backoff_ms}ms</span> : null}
                </div>
              ))}
            </div>
          ) : actionCount > 0 ? (
            <div className="action-trace-note">
              本轮没有触发自动重试；危险命令和有副作用动作不会自动重试。
            </div>
          ) : (
            <div className="action-trace-note">
              本轮失败发生在工具调用前，未触发可重试动作。
            </div>
          )}
          {validationCorrection.attempts > 0 && (
            <div className="action-trace-note">
              工具参数校验未通过后已交由模型修正
              {validationCorrection.exhausted ? "，达到上限后停止，未执行无效调用。" : "，无效调用未进入执行器。"}
            </div>
          )}
          {toolRecoveryEvents.length > 0 && (
            <div className="action-trace-note">
              原调用未被盲目重复，模型已收到失败证据并继续选择安全替代方案。
            </div>
          )}
          {tracking.taskId && (
            <div className="action-trace-note">
              <b>任务跟踪 · {tracking.taskId}</b>
              <span className="tracking-status">
                状态 {tracking.status || "unknown"}
                {tracking.mode ? ` · ${tracking.mode}` : ""}
                {tracking.progress?.percent != null ? ` · ${tracking.progress.percent}%` : ""}
              </span>
              {tracking.stallRisk && <span className="action-trace-pill warn tracking-stall-pill">可能停滞</span>}
              {!tracking.done && (
                <div className="tracking-card-wrap">
                  <TaskTrackingCard tracking={tracking.summary} />
                </div>
              )}
              {tracking.done && (
                <div className="tracking-summary">
                  设备 {String(tracking.taskSummary.succeeded_devices ?? 0)} 成功 / {String(tracking.taskSummary.failed_devices ?? 0)} 失败 / {String(tracking.taskSummary.skipped_devices ?? 0)} 跳过；
                  发现 {String(tracking.taskSummary.findings_critical ?? 0)} critical · {String(tracking.taskSummary.findings_warning ?? 0)} warning · {String(tracking.taskSummary.findings_info ?? 0)} info。
                  {tracking.suggestedNextAction === "analyze_artifacts" ? " 下一步：读取原始采集制品并分析。" : ""}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {Array.isArray(summaries) && summaries.length > 0 && (
        <div className="chat-source-summary" data-testid="inline-source-summary">
          <b>参考来源 · {summaries.length} 个</b>
          <div className="chat-source-list">
            {summaries.slice(0, 6).map((s: SourceSummary, i: number) => (
              <span className="chat-source-chip" key={s.citation_id || s.chunk_id || s.source_id || i}>
                {s.citation_id ? `${s.citation_id} · ` : ""}
                {s.evidence_type === "memory" ? "记忆" : "知识"} · {s.title || s.source_id}
                <span className="score">{s.score != null ? ` ${Number(s.score).toFixed(2)}` : ""}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="result-actions">
          <button type="button" className="run-detail-button" onClick={() => void rememberAnswer()} disabled={!!saving}>
            {saving === "memory" ? "记录中…" : "记住结论"}
          </button>
          <button type="button" className="run-detail-button" onClick={() => void saveAsKnowledge()} disabled={!!saving}>
            {saving === "knowledge" ? "保存中…" : "存为知识"}
          </button>
          {hasFailedTool && onRetryAlternative && (
            <button type="button" className="run-detail-button" onClick={onRetryAlternative}>
              换方案继续
            </button>
          )}
          {isFailed && onRetryOriginal && (
            <button type="button" className="run-detail-button" onClick={onRetryOriginal}>
              重试原任务
            </button>
          )}
          {Array.isArray(summaries) && summaries.length > 0 && (
            <span className="run-detail-info">来源 ({summaries.length})</span>
          )}
        </div>

      {isFailed && result?.errors && result.errors.length > 0 && (
        <details className="mt-2">
          <summary className="wb-run-detail">技术详情</summary>
          <div className="text-xs mono mt-1 technical-error">
            {(result?.errors ?? []).join("\n")}
          </div>
        </details>
      )}
    </div>
  );
});

function toolCallSummary(calls: ToolCallResult[]): string {
  const failed = calls.filter((tc) => !tc.ok).length;
  const recovered = calls.some((tc) => !tc.ok && calls.some((other) => other.ok && other.tool_id === tc.tool_id));
  const primary = calls.find((tc) => tc.ok) ?? calls[0];
  const label = primary ? toolLabel(primary.tool_id) : "工具调用";
  if (failed > 0 && recovered) return `${label}已完成，${failed} 次内部重试已自动恢复`;
  if (failed > 0) return `${label}需要关注，${failed} 次调用未完成`;
  return `${label}已完成`;
}
