import React, { useState, useRef, useEffect, useCallback } from "react";
import { agentApi, sessionsApi, settingsApi, sseApi } from "../../api";
import { apiRequest, getApiAccessToken } from "../../api/client";
import { useSessionStore } from "../../stores/session";
import { useWorkbenchStore, type ChatMsg } from "../../stores/workbench";
import { useToastStore } from "../../stores/toast";
import { isApiError } from "../../types";
import type { AgentResult, ToolCallResult, InlineToolCall } from "../../types";
import { sanitizeAssistantText, filterStreamingThink, toolLabel } from "../../utils/displayText";
import { beginModelStep, discardToolCallDraft, finalizeStreamText } from "../../utils/agentStream";
import { humanFailure } from "../../utils/humanizeError";
import "./WorkbenchHighlight";
import { agentResultFromWsDone } from "../../utils/wsResult";
import { notifyRunCompleted } from "../../utils/appEvents";
import { IconAlert, IconSend } from "../../components/Icon";
import { ApprovalBubble } from "../../components/ApprovalBubble";
import { RuntimeEventTimeline } from "../../components/RuntimeEventTimeline";
import "../../components/RuntimeEventTimeline.css";
import { formatFileSize } from "../../utils/format";
import { QUICK_CHIPS } from "./WorkbenchQuickChips";
import { MessageRow } from "./components/MessageRow";

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

  // Stable retry handler passed to message rows — refs never change, so this
  // callback keeps a constant reference and avoids re-rendering every row.
  const handleRetryOriginal = useCallback((text: string) => {
    onSendRef.current(text);
  }, []);

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
    const wsHost = window.location.host; // Includes port (5273 in dev, 8011 in prod)
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
          toast({ kind: "error", title: "请求失败", body: humanFailure(res.error_type ?? "", res.errors?.[0] ?? "").msg });
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
              <MessageRow
                key={m.message_id || m.id}
                m={m}
                idx={idx}
                total={(visibleHistory ?? []).length}
                lastUserInput={lastUserInput}
                onRetryOriginal={handleRetryOriginal}
              />
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
            <span>{humanFailure(lastResult.error_type, lastResult.errors?.[0] ?? "请求失败").msg}</span>
            {humanFailure(lastResult.error_type, lastResult.errors?.[0] ?? "").retryable && (
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
