import React, { useState, useRef, useEffect, useCallback } from "react";
import { sessionsApi, settingsApi, sseApi } from "../../api";
import { getApiAccessToken, realtimeEndpoint } from "../../api/client";
import type { SSEConnection } from "../../api/sse";
import { useSessionStore } from "../../stores/session";
import { useWorkbenchStore, type ChatMsg } from "../../stores/workbench";
import { useToastStore } from "../../stores/toast";
import { humanFailure } from "../../utils/humanizeError";
import "./WorkbenchHighlight";
import { IconAlert, IconSend } from "../../components/Icon";
import { ApprovalBubble } from "../../components/ApprovalBubble";
import { RuntimeEventTimeline } from "../../components/RuntimeEventTimeline";
import "../../components/RuntimeEventTimeline.css";
import { formatFileSize } from "../../utils/format";
import { QUICK_CHIPS } from "./WorkbenchQuickChips";
import { MessageRow } from "./components/MessageRow";
import { scopedLocalStorageKey } from "../../utils/userScope";
import { useWorkbenchSend, type PendingAttachment } from "../../hooks/useWorkbenchSend";

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

  // Input draft persistence: save to localStorage debounced, restore on mount
  const draftKey = scopedLocalStorageKey(`draft-${currentSessionId ?? "_scratch"}`);
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

  const { send: onSend, stop: stopGeneration } = useWorkbenchSend({
    workspaceId: currentWorkspaceId,
    sessionId: currentSessionId,
    input,
    attachments,
    sending,
    visionSupported: llmHealth.visionSupported,
    setInput,
    setAttachments,
    clearDraft,
    prepareToSend: () => { userScrolledUpRef.current = false; },
    keepAtBottom,
    toast,
    pendingAutoMetadataRef,
  });

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
    <div className="wb-shell">
      {/* ── Header bar ── */}
      <div className="wb-header">
        <div className="wb-header-context">
          <span className="wb-header-kicker">智能运维工作台</span>
          <div className="wb-header-status">
            <span className={"dot " + (llmHealth.connected ? (llmHealth.recentFailure ? "warn" : "ok") : "err")} />
            <span>{llmStatusLabel}</span>
          </div>
        </div>
        <div className="wb-header-actions">
          <span className="wb-header-session" title={currentSessionId || ""}>
            {currentSessionId ? `会话 ${currentSessionId.slice(0, 8)}` : "待创建会话"}
          </span>
          {currentSessionId && visibleHistory && visibleHistory.length > 0 && (
            <button className="wb-export-btn" title="导出对话" onClick={() => {
              const md = visibleHistory.map((m) =>
                `## ${m.role === "user" ? "用户" : "AI"}\n\n${m.text}\n\n---\n`
              ).join("\n");
              const blob = new Blob([md], { type: "text/markdown" });
              const a = document.createElement("a");
              a.href = URL.createObjectURL(blob);
              a.download = `session-${currentSessionId.slice(0, 8)}-${new Date().toISOString().slice(0, 10)}.md`;
              a.click();
              setTimeout(() => URL.revokeObjectURL(a.href), 100);
            }}>导出记录</button>
          )}
        </div>
      </div>
      <div className="wb-context-rail" aria-label="运行说明">
        <span><b>分步骤执行</b>，处理过程清晰可查</span>
        <span>写操作结果不确定时会自动暂停，等待核对</span>
        {currentWorkspaceId && <span>当前工作区：{currentWorkspaceId}</span>}
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
            <h2>{currentSessionId ? "任务工作台" : "请先新建会话"}</h2>
            <p>{currentSessionId ? "输入故障现象、配置片段或排查目标，智能体会按时间顺序展示处理过程。" : "点击左侧“会话”旁的 +，创建会话后即可开始。"}</p>
            <div className="wb-empty-chips">
              {QUICK_CHIPS.map((c) => (
                <button key={c.label} className="wb-input-chip" type="button" onClick={() => pickChip(c.prompt)} title={currentSessionId ? c.prompt : "请先新建会话"} disabled={!currentSessionId}>
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
        if (!currentSessionId || sending || !lastUserInput) return null;
        const lastAssistant = [...(visibleHistory ?? [])].reverse().find((m) => m.role === "assistant");
        const lastResult = lastAssistant?.result;
        if (!lastResult) return null;
        if (lastResult.metadata?.execution_outcome === "unknown") return null;
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
                {a.uploading ? <span className="spinner wb-attachment-spinner" /> : a.previewUrl ? <img className="wb-attachment-preview" src={a.previewUrl} alt="待识别图片" /> : "📄"}
                <span className="wb-attachment-name">{a.name}</span>
                <button onClick={() => removeAttachment(a.id)} className="wb-attachment-remove" type="button">&times;</button>
              </span>
            ))}
          </div>
        )}
        <div className="wb-input-row">
            <input ref={fileInputRef} type="file" multiple disabled={!currentSessionId || sending} accept=".txt,.md,.json,.csv,.tsv,.log,.conf,.cfg,.yaml,.yml,.xml,.html,.htm,.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.gif,.webp" onChange={(e) => { if (e.target.files) { addFiles(e.target.files); e.target.value = ""; } }} className="wb-file-input" />
            <button className="wb-attach-btn" onClick={pickFile} disabled={!currentSessionId || sending} title={currentSessionId ? "上传常见文档、表格、演示文稿、配置或图片（单文件 100 MB，图片 5 MB）" : "请先新建会话"} type="button">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M8.5 1.5v9M5 5l3.5-3.5L12 5M2.5 10v2.5a1 1 0 001 1h9a1 1 0 001-1V10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </button>
            <textarea
              ref={inputRef}
              className="wb-input wb-input-content"
              placeholder={currentSessionId ? "输入主机名、IP 或排查目标… (Enter 发送, Shift+Enter 换行)" : "请先点击左侧 + 新建会话"}
              value={input}
              onChange={(e) => handleInputChange(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); onSend(); } }}
              disabled={!currentSessionId || sending}
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
                disabled={!currentSessionId || (!input.trim() && attachments.length === 0)}
                data-testid="btn-send"
                type="button"
                aria-label="发送"
                title="Enter 发送"
              >
                <IconSend size={14} />
              </button>
            )}
          </div>
        <div className="wb-composer-meta">
          <span>Enter 发送 · Shift + Enter 换行 · 支持拖拽或粘贴图片</span>
          <span>{attachments.length > 0 ? `已附加 ${attachments.length}/8 个文件` : "当前会话中的操作会经过安全检查"}</span>
        </div>
      </div>

      {/* ── Inline approval bubble for high-risk tools ── */}
      <ApprovalBubble />
    </div>
  );
}
