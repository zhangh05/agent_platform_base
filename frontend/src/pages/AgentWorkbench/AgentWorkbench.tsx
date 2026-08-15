import React, { lazy, memo, Suspense, useState, useRef, useEffect, useCallback, useMemo } from "react";
import { jobsApi, sessionsApi, settingsApi, sseApi } from "../../api";
import { getApiAccessToken, realtimeEndpoint } from "../../api/client";
import type { SSEConnection } from "../../api/sse";
import { useSessionStore } from "../../stores/session";
import { useWorkbenchStore, type ChatMsg } from "../../stores/workbench";
import { useToastStore } from "../../stores/toast";
import { humanFailure } from "../../utils/humanizeError";
import "./WorkbenchHighlight";
import { IconAlert, IconAttachment, IconChat, IconChevronDown, IconClose, IconDocument, IconHistory, IconRefresh, IconSend, IconStop } from "../../components/Icon";
import { ApprovalBubble } from "../../components/ApprovalBubble";
import "../../components/RuntimeEventTimeline.css";
import "./AgentWorkbench.css";
import { formatFileSize } from "../../utils/format";
import { QUICK_CHIPS } from "./WorkbenchQuickChips";
import { MessageRow } from "./components/MessageRow";
import { scopedLocalStorageKey } from "../../utils/userScope";
import { useWorkbenchSend, type PendingAttachment } from "../../hooks/useWorkbenchSend";
import { useActiveTurn } from "../../hooks/useActiveTurn";
import { TaskProgressPanel } from "./components/TaskProgressPanel";

const RuntimeEventTimeline = lazy(() => import("../../components/RuntimeEventTimeline").then((m) => ({ default: m.RuntimeEventTimeline })));

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
// Keep typing on the React path; synchronous localStorage writes are deferred.
const DRAFT_WRITE_DEBOUNCE_MS = 180;

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
  const switchSession = useWorkbenchStore((s) => s.switchSession);
  const mergeFromBackend = useWorkbenchStore((s) => s.mergeFromBackend);

  const [viewMode, setViewMode] = useState<ViewMode>("chat");
  const [progressPanelCollapsed, setProgressPanelCollapsed] = useState(false);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);

  // ── Scroll architecture (v4.1) ──
  // A plain scroll container is enough for the capped chat history and avoids
  // virtual-list measurement jumps while an assistant answer is streaming.
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const userScrolledUpRef = useRef(false);    // true = user intentionally scrolled up
  const atBottomRef = useRef(true);
  const sendingRef = useRef(false);
  const scrollFrameRef = useRef<number | null>(null);

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
    if (userScrolledUpRef.current || scrollFrameRef.current !== null) return;
    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      const el = chatRef.current;
      if (!el || userScrolledUpRef.current) return;
      el.scrollTop = el.scrollHeight;
      atBottomRef.current = true;
      setShowScrollBtn(false);
    });
  }, []);

  useEffect(() => () => {
    if (scrollFrameRef.current !== null) cancelAnimationFrame(scrollFrameRef.current);
  }, []);

  const handleScrollBtnClick = useCallback(() => {
    userScrolledUpRef.current = false;
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
    atBottomRef.current = true;
    setShowScrollBtn(false);
  }, []);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [llmHealth, setLlmHealth] = useState<{ connected: boolean; provider?: string; model?: string; recentFailure?: string; visionSupported?: boolean }>({ connected: false });
  const toast = useToastStore((s) => s.show);
  const { job: activeJob, refresh: refreshActiveTurn } = useActiveTurn(currentWorkspaceId, currentSessionId);
  const durableTurn = activeJob?.metadata?.active_turn;
  const turnRunning = sending || activeJob?.status === "running";
  // System and message streams use separate refs for system WebSocket and message WebSocket
  // to prevent race conditions where message streaming overwrites the
  // system WS reference and vice versa.
  const systemWsRef = useRef<WebSocket | null>(null);
  const pendingAutoMetadataRef = useRef<Record<string, unknown> | null>(null);
  const onSendRef = useRef<(text?: string, metadata?: Record<string, unknown>) => void>(() => {});

  // Stable retry handler passed to message rows — refs never change, so this
  // callback keeps a constant reference and avoids re-rendering every row.
  const handleRetryOriginal = useCallback((text: string) => {
    onSendRef.current(text);
  }, []);

  // Preserve current session id ref for cleanup
  const prevSessionId = useRef(currentSessionId);
  useEffect(() => { prevSessionId.current = currentSessionId; });


  // LLM health — load once on mount
  useEffect(() => {
    settingsApi.llmStatus().then((s) => {
      if (!s) return;
      setLlmHealth({
        connected: s.connected, provider: s.provider || s.provider_type || "",
        model: s.model || "", recentFailure: s.recent_failure?.error_type ? s.recent_failure.error_summary : undefined,
        visionSupported: s.vision_supported,
      });
    }).catch(() => {});
  }, []);

  // ── Persistent system WebSocket — replaces all polling ──
  // Use systemWsRef for the persistent stream so message streaming cannot overwrite it.
  useEffect(() => {
    if (!currentWorkspaceId) return;
    const wsUrl = realtimeEndpoint("/ws/agent");
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
  // Input remains fully controlled; persistence is batched so keypresses never
  // synchronously serialize to localStorage on the browser main thread.
  const draftKey = scopedLocalStorageKey("draft-" + (currentSessionId ?? "_scratch"));
  const draftWriteTimerRef = useRef<number | null>(null);
  const pendingDraftWriteRef = useRef<{ key: string; value: string } | null>(null);
  const flushDraftWrite = useCallback(() => {
    if (draftWriteTimerRef.current !== null) {
      window.clearTimeout(draftWriteTimerRef.current);
      draftWriteTimerRef.current = null;
    }
    const pending = pendingDraftWriteRef.current;
    pendingDraftWriteRef.current = null;
    if (pending) safeSetLocal(pending.key, pending.value);
  }, []);
  const scheduleDraftWrite = useCallback((key: string, value: string) => {
    pendingDraftWriteRef.current = { key, value };
    if (draftWriteTimerRef.current !== null) window.clearTimeout(draftWriteTimerRef.current);
    draftWriteTimerRef.current = window.setTimeout(flushDraftWrite, DRAFT_WRITE_DEBOUNCE_MS);
  }, [flushDraftWrite]);
  useEffect(() => {
    const saved = safeGetLocal(draftKey);
    setInput(saved ?? "");
    return () => flushDraftWrite();
  }, [draftKey, flushDraftWrite]);
  useEffect(() => () => flushDraftWrite(), [flushDraftWrite]);
  const handleInputChange = useCallback((val: string) => {
    setInput(val);
    scheduleDraftWrite(draftKey, val);
  }, [draftKey, scheduleDraftWrite]);
  // Clear draft after successful send and cancel a pending stale write.
  const clearDraft = useCallback(() => {
    if (pendingDraftWriteRef.current?.key === draftKey) {
      pendingDraftWriteRef.current = null;
      if (draftWriteTimerRef.current !== null) {
        window.clearTimeout(draftWriteTimerRef.current);
        draftWriteTimerRef.current = null;
      }
    }
    safeRemoveLocal(draftKey);
  }, [draftKey]);

  const { send: onSend, stop: stopGeneration } = useWorkbenchSend({
    workspaceId: currentWorkspaceId,
    sessionId: currentSessionId,
    input,
    attachments,
    sending: turnRunning,
    visionSupported: llmHealth.visionSupported,
    setInput,
    setAttachments,
    clearDraft,
    prepareToSend: () => { userScrolledUpRef.current = false; },
    keepAtBottom,
    toast,
    pendingAutoMetadataRef,
  });

  const latestAssistant = [...visibleHistory].reverse().find((message) => message.role === "assistant");
  // The progress panel consumes stages, tool calls and terminal result, not the
  // accumulated token text. Keep its prop stable while plain streaming text updates.
  const progressAssistant = useMemo<ChatMsg | undefined>(() => {
    if (!latestAssistant) return undefined;
    const { id, role, status, created_at, runtimeEvents, toolCalls, result } = latestAssistant;
    return { id, role, text: "", status, created_at, runtimeEvents, toolCalls, result };
  }, [
    latestAssistant?.id, latestAssistant?.status, latestAssistant?.created_at,
    latestAssistant?.runtimeEvents, latestAssistant?.toolCalls, latestAssistant?.result,
  ]);
  const handleShowTimeline = useCallback(() => setViewMode("timeline"), []);
  const handleToggleProgressPanel = useCallback(() => {
    setProgressPanelCollapsed((value) => !value);
  }, []);

  const latestUser = [...visibleHistory].reverse().find((message) => message.role === "user");
  const sessionTitle = latestUser?.text.trim().split("\n")[0].slice(0, 32) || "新会话";
  const terminalJobRef = useRef<string>("");

  useEffect(() => {
    const runId = String(durableTurn?.run_id || "");
    if (!currentSessionId || !currentWorkspaceId || !runId || activeJob?.status === "running") return;
    const marker = `${activeJob?.job_id || ""}:${runId}:${activeJob?.status || ""}`;
    if (terminalJobRef.current === marker) return;
    terminalJobRef.current = marker;
    sessionsApi.messages(currentSessionId, currentWorkspaceId)
      .then((response) => {
        if (response.messages?.length) mergeFromBackend(currentSessionId, response.messages);
        return useWorkbenchStore.getState().loadRunDetail(currentWorkspaceId, runId, currentSessionId);
      })
      .catch(() => {});
  }, [activeJob?.job_id, activeJob?.status, currentSessionId, currentWorkspaceId, durableTurn?.run_id, mergeFromBackend]);

  const stopActiveTurn = useCallback(() => {
    if (sending) {
      stopGeneration();
      return;
    }
    if (!activeJob?.job_id || !currentWorkspaceId) return;
    void jobsApi.cancel(activeJob.job_id, currentWorkspaceId)
      .then(() => refreshActiveTurn())
      .catch(() => {});
  }, [activeJob?.job_id, currentWorkspaceId, refreshActiveTurn, sending, stopGeneration]);

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
    if (autoRaw && currentWorkspaceId && currentSessionId) {
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
        void onSendRef.current(prompt, payload.metadata || {});
      }, AUTO_SEND_DELAY_MS);
      return () => {
        if (timer) clearTimeout(timer);
      };
    }
  }, [currentSessionId, currentWorkspaceId]); // do NOT include onSend — use ref to avoid re-render killing timeout

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
    if (!currentSessionId || !currentWorkspaceId || typeof fetch === "undefined") return;
    let closed = false;
    let es: SSEConnection | null = null;
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

  useEffect(() => {
    onSendRef.current = onSend;
  }, [onSend]);

  function pickChip(prompt: string) {
    if (!currentSessionId) return;
    setInput(prompt);
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  // ── File upload ──

  function addFiles(files: FileList | File[]) {
    if (!currentSessionId) return;
    const maxFileBytes = 100 * 1024 * 1024;
    const maxImageBytes = 5 * 1024 * 1024;
    const available = Math.max(0, 8 - attachments.length);
    const accepted: File[] = [];
    const skipped: string[] = [];
    for (const file of Array.from(files)) {
      if (accepted.length >= available) {
        skipped.push(`${file.name}：一次最多附加 8 个文件`);
      } else if (file.type.startsWith("image/") && file.size > maxImageBytes) {
        skipped.push(`${file.name}：图片不能超过 5 MB`);
      } else if (file.size > maxFileBytes) {
        skipped.push(`${file.name}：文件不能超过 100 MB`);
      } else {
        accepted.push(file);
      }
    }
    if (skipped.length) toast({ kind: "warning", title: "部分文件未添加", body: skipped.slice(0, 3).join("；") });
    setAttachments((prev) => [
      ...prev,
      ...accepted.map((f) => ({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        name: f.name, size: formatFileSize(f.size), file: f,
        previewUrl: f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined,
      })),
    ]);
  }

  function removeAttachment(id: string) {
    setAttachments((prev) => {
      const removed = prev.find((a) => a.id === id);
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return prev.filter((a) => a.id !== id);
    });
  }

  function pickFile() {
    if (!currentSessionId) return;
    fileInputRef.current?.click();
  }

  // Drag-drop handler
  const handleDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); }, []);
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (!currentSessionId) return;
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  }, [currentSessionId]);

  // Paste handler — capture images from clipboard
  useEffect(() => {
    const handler = (e: ClipboardEvent) => {
      if (!currentSessionId) return;
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
  }, [currentSessionId]);

  const llmStatusLabel = llmHealth.connected
    ? llmHealth.recentFailure ? "模型可用 · 最近一次请求超时，可重试" : `模型可用 · ${llmHealth.model || llmHealth.provider || "在线"}`
    : "模型不可用";

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
    <div className={progressPanelCollapsed ? "wb-shell is-progress-collapsed" : "wb-shell"}>
      <section className="wb-conversation-column">
        <header className="wb-header">
          <div className="wb-header-context">
            <span className="wb-header-kicker">{viewMode === "chat" ? "当前会话" : "运行记录"}</span>
            <h1 title={sessionTitle}>{viewMode === "chat" ? sessionTitle : "完整时间线"}</h1>
          </div>
          <div className="wb-header-actions">
            <span className="wb-header-status">
              <span className={"dot " + (llmHealth.connected ? (llmHealth.recentFailure ? "warn" : "ok") : "err")} />
              {llmStatusLabel}
            </span>
            <button
              type="button"
              className={`wb-mode-btn ${viewMode === "chat" ? "active" : ""}`}
              onClick={() => setViewMode("chat")}
              data-testid="view-chat"
            >
              <IconChat size={15} />对话
            </button>
            <button
              type="button"
              className={`wb-mode-btn ${viewMode === "timeline" ? "active" : ""}`}
              onClick={() => setViewMode("timeline")}
              data-testid="view-timeline"
            >
              <IconHistory size={15} />时间线
            </button>
            {currentSessionId && visibleHistory.length > 0 ? (
              <button className="wb-export-btn" title="导出对话" onClick={() => {
                const md = visibleHistory.map((m) => `## ${m.role === "user" ? "用户" : "AI"}\n\n${m.text}\n\n---\n`).join("\n");
                const blob = new Blob([md], { type: "text/markdown" });
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = `session-${currentSessionId.slice(0, 8)}-${new Date().toISOString().slice(0, 10)}.md`;
                a.click();
                setTimeout(() => URL.revokeObjectURL(a.href), 100);
              }}>导出</button>
            ) : null}
          </div>
        </header>

        <div className="wb-chat" data-testid="chat-stream">
          {viewMode === "timeline" ? (
            <Suspense fallback={<div className="wb-timeline-loading" role="status">正在加载时间线…</div>}>
              <RuntimeEventTimeline messages={visibleHistory} />
            </Suspense>
          ) : visibleHistory.length === 0 && !turnRunning ? (
            <div className="wb-empty" data-testid="workbench-empty">
              <span className="wb-empty-kicker">开始一次可靠的智能运维任务</span>
              <h2>{currentSessionId ? "今天需要处理什么？" : "请先新建会话"}</h2>
              <p>{currentSessionId ? "描述问题、上传文件或给出目标。联智中枢会调用合适的工具，并在右侧实时展示处理进度与证据。" : "点击左侧“新会话”，创建后即可开始。"}</p>
              <div className="wb-empty-chips">
                {QUICK_CHIPS.map((chip) => (
                  <button key={chip.label} className="wb-input-chip" type="button" onClick={() => pickChip(chip.prompt)} title={currentSessionId ? chip.prompt : "请先新建会话"} disabled={!currentSessionId}>
                    {chip.label}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div ref={chatRef} className="wb-chat-list" role="log" aria-live={turnRunning ? "polite" : "off"} onScroll={handleChatScroll}>
              {visibleHistory.map((message, index) => (
                <MessageRow key={message.message_id || message.id} m={message} idx={index} total={visibleHistory.length} lastUserInput={lastUserInput} onRetryOriginal={handleRetryOriginal} />
              ))}
              {activeJob?.status === "running" && latestAssistant?.status !== "streaming" ? (
                <div className="wb-restored-run" role="status">
                  <span className="typing-indicator"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></span>
                  <span>任务仍在服务器处理中，页面已重新接入实时状态。</span>
                </div>
              ) : null}
            </div>
          )}
          {showScrollBtn ? (
            <button className="scroll-bottom-btn" onClick={handleScrollBtnClick} title="回到底部" type="button" aria-label="回到底部">
              <IconChevronDown size={15} />
            </button>
          ) : null}
        </div>

        {(() => {
          if (!currentSessionId || turnRunning || !lastUserInput) return null;
          const lastResult = latestAssistant?.result;
          if (!lastResult || lastResult.metadata?.execution_outcome === "unknown" || lastResult.ok) return null;
          return (
            <div className="wb-retry-bar">
              <IconAlert size={13} />
              <span>{humanFailure(lastResult.error_type, lastResult.errors?.[0] ?? "请求失败").msg}</span>
              {humanFailure(lastResult.error_type, lastResult.errors?.[0] ?? "").retryable ? (
                <button type="button" onClick={() => onSendRef.current(lastUserInput)} data-testid="retry-btn"><IconRefresh size={13} />重试</button>
              ) : null}
            </div>
          );
        })()}

        <div className="wb-input-bar" onDragOver={handleDragOver} onDrop={handleDrop}>
          {attachments.length > 0 ? (
            <div className="wb-attachments">
              {attachments.map((attachment) => (
                <span key={attachment.id} className="tag wb-attachment-tag">
                  {attachment.uploading ? <span className="spinner wb-attachment-spinner" /> : attachment.previewUrl ? <img className="wb-attachment-preview" src={attachment.previewUrl} alt="待识别图片" /> : <IconDocument size={14} />}
                  <span className="wb-attachment-name">{attachment.name}</span>
                  <button onClick={() => removeAttachment(attachment.id)} className="wb-attachment-remove" type="button" aria-label={`移除 ${attachment.name}`}><IconClose size={12} /></button>
                </span>
              ))}
            </div>
          ) : null}
          <div className="wb-input-row">
            <textarea
              ref={inputRef}
              className="wb-input wb-input-content"
              placeholder={currentSessionId ? "输入问题或添加文件" : "请先点击左侧 + 新建会话"}
              value={input}
              onChange={(event) => handleInputChange(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); onSend(); } }}
              disabled={!currentSessionId || turnRunning}
              rows={2}
              data-testid="chat-input"
              spellCheck={false}
            />
            <div className="wb-composer-actions">
              <input ref={fileInputRef} type="file" multiple disabled={!currentSessionId || turnRunning} accept=".txt,.md,.json,.csv,.tsv,.log,.conf,.cfg,.yaml,.yml,.xml,.html,.htm,.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.gif,.webp" onChange={(event) => { if (event.target.files) { addFiles(event.target.files); event.target.value = ""; } }} className="wb-file-input" />
              <button className="wb-attach-btn" onClick={pickFile} disabled={!currentSessionId || turnRunning} title={currentSessionId ? "添加文件" : "请先新建会话"} type="button">
                <IconAttachment size={16} /><span>添加文件</span>
              </button>
              {turnRunning ? (
                <button className="wb-stop" onClick={stopActiveTurn} title="停止任务" type="button" data-testid="btn-stop"><IconStop size={14} weight="fill" /><span>停止</span></button>
              ) : (
                <button className="wb-send" onClick={() => onSend()} disabled={!currentSessionId || (!input.trim() && attachments.length === 0)} data-testid="btn-send" type="button" aria-label="发送" title="Enter 发送">
                  <IconSend size={17} />
                </button>
              )}
            </div>
          </div>
          <div className="wb-composer-meta">
            <span>Enter 发送 · Shift + Enter 换行</span>
            <span>{attachments.length > 0 ? `已添加 ${attachments.length}/8 个文件` : "操作会经过权限与安全检查"}</span>
          </div>
        </div>
      </section>

      <TaskProgressPanel
        latestAssistant={progressAssistant}
        snapshot={durableTurn}
        onShowTimeline={handleShowTimeline}
        collapsed={progressPanelCollapsed}
        onToggleCollapsed={handleToggleProgressPanel}
      />

      {/* ── Inline approval bubble for high-risk tools ── */}
      <ApprovalBubble />
    </div>
  );
}
