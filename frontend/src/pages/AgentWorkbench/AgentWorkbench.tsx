import React, { lazy, Suspense, useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from "react";
import { jobsApi, sessionsApi, settingsApi } from "../../api";
import { apiRequest } from "../../api/client";
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

interface WorkbenchSkill {
  extension_id: string;
  skill_id: string;
  name: string;
  description: string;
  resources: Array<{ resource_id: string; name: string; description: string; kind: string }>;
  default_resource_ids: string[];
  selection_mode: "single" | "multiple";
}

const EMPTY_CHAT_MESSAGES: ChatMsg[] = [];

/* ── timing constants ── */
// Auto-send delay for prompts pulled out of sessionStorage (e.g. workbench_auto_prompt)
// — short enough to feel responsive, long enough for the input frame to mount.
const AUTO_SEND_DELAY_MS = 500;
// Health probes invoke the provider's real chat path. Retry only while the
// provider is unavailable, with bounded backoff, so a backend warm-up cannot
// leave the workbench permanently showing a stale unavailable state.
const LLM_HEALTH_RETRY_INITIAL_MS = 1000;
const LLM_HEALTH_RETRY_MAX_MS = 30_000;
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
  const [workbenchSkills, setWorkbenchSkills] = useState<WorkbenchSkill[]>([]);
  const [skillCatalogLoaded, setSkillCatalogLoaded] = useState(false);
  const [selectedSkillKey, setSelectedSkillKey] = useState("");
  const [selectedResourceIds, setSelectedResourceIds] = useState<string[]>([]);
  const [selectionSessionId, setSelectionSessionId] = useState("");
  const selectedSkill = useMemo(
    () => workbenchSkills.find((item) => `${item.extension_id}:${item.skill_id}` === selectedSkillKey),
    [selectedSkillKey, workbenchSkills],
  );
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
  const approvalRefreshTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!currentWorkspaceId) return;
    const params = { workspace_id: currentWorkspaceId, enabled: "1" };
    setSkillCatalogLoaded(false);
    apiRequest<{ skills: WorkbenchSkill[] }>({ method: "GET", url: "/workbench/skills", params })
      .then((response) => setWorkbenchSkills(response.skills || []))
      .catch(() => setWorkbenchSkills([]))
      .finally(() => setSkillCatalogLoaded(true));
  }, [currentWorkspaceId]);
  useEffect(() => {
    if (!currentSessionId) { setSelectedSkillKey(""); setSelectedResourceIds([]); setSelectionSessionId(""); return; }
    const key = scopedLocalStorageKey(`workbench_skill:${currentSessionId}`);
    try {
      const saved = JSON.parse(localStorage.getItem(key) || "{}") as { skill_key?: string; resource_ids?: string[] };
      setSelectedSkillKey(saved.skill_key || "");
      setSelectedResourceIds(Array.isArray(saved.resource_ids) ? saved.resource_ids : []);
      setSelectionSessionId(currentSessionId);
    } catch { setSelectedSkillKey(""); setSelectedResourceIds([]); setSelectionSessionId(currentSessionId); }
  }, [currentSessionId]);
  useEffect(() => {
    if (!currentSessionId || selectionSessionId !== currentSessionId) return;
    const key = scopedLocalStorageKey(`workbench_skill:${currentSessionId}`);
    try { localStorage.setItem(key, JSON.stringify({ skill_key: selectedSkillKey, resource_ids: selectedResourceIds })); } catch { /* noop */ }
  }, [currentSessionId, selectedResourceIds, selectedSkillKey, selectionSessionId]);
  useEffect(() => {
    if (!skillCatalogLoaded || !selectedSkillKey) return;
    if (!selectedSkill) {
      setSelectedSkillKey("");
      setSelectedResourceIds([]);
      return;
    }
    const available = new Set(selectedSkill.resources.map((item) => item.resource_id));
    const valid = selectedResourceIds.filter((item) => available.has(item));
    const normalized = valid.length ? valid : selectedSkill.default_resource_ids;
    if (normalized.length !== selectedResourceIds.length || normalized.some((item, index) => item !== selectedResourceIds[index])) {
      setSelectedResourceIds(normalized);
    }
  }, [selectedResourceIds, selectedSkill, selectedSkillKey, skillCatalogLoaded]);

  useEffect(() => () => {
    if (approvalRefreshTimerRef.current !== null) {
      window.clearTimeout(approvalRefreshTimerRef.current);
      approvalRefreshTimerRef.current = null;
    }
  }, []);

  const refreshAfterApproval = useCallback(() => {
    const sessionId = currentSessionId;
    const workspaceId = currentWorkspaceId;
    if (!sessionId || !workspaceId) return;

    if (approvalRefreshTimerRef.current !== null) {
      window.clearTimeout(approvalRefreshTimerRef.current);
      approvalRefreshTimerRef.current = null;
    }

    // Resolve returns before the server-side continuation has finished. The
    // Resolve returns before the server-side continuation has finished. This
    // bounded read-only hydration observes its durable result.
    const knownAssistantKeys = new Set(
      (useWorkbenchStore.getState().bySession[sessionId] ?? [])
        .filter((message) => message.role === "assistant")
        .map((message) => message.message_id ?? `${message.run_id ?? ""}:${message.created_at}:${message.text}`),
    );
    let attempts = 0;
    const refresh = () => {
      void sessionsApi.messages(sessionId, workspaceId)
        .then((res) => {
          const messages = res.messages ?? [];
          if (messages.length) mergeFromBackend(sessionId, messages);
          const receivedContinuationResult = messages.some((message) => {
            if (message.role !== "assistant") return false;
            const key = message.message_id ?? `${message.run_id ?? ""}:${message.created_at}:${message.content}`;
            return !knownAssistantKeys.has(key);
          });
          if (receivedContinuationResult || attempts >= 30) {
            approvalRefreshTimerRef.current = null;
            return;
          }
          approvalRefreshTimerRef.current = window.setTimeout(refresh, 1000);
        })
        .catch(() => {
          if (attempts >= 30) {
            approvalRefreshTimerRef.current = null;
            return;
          }
          approvalRefreshTimerRef.current = window.setTimeout(refresh, 1000);
        });
      attempts += 1;
    };
    refresh();
  }, [currentSessionId, currentWorkspaceId, mergeFromBackend]);

  const [viewMode, setViewMode] = useState<ViewMode>("chat");
  const [progressPanelCollapsed, setProgressPanelCollapsed] = useState(false);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [headerCollapsed, setHeaderCollapsed] = useState(false);


  // ── Scroll architecture (v4.1) ──
  // A plain scroll container is enough for the capped chat history and avoids
  // virtual-list measurement jumps while an assistant answer is streaming.
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const userScrolledUpRef = useRef(false);    // true = user intentionally scrolled up
  const atBottomRef = useRef(true);
  const chatRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const handleChatScroll = useCallback(() => {
    const el = chatRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
    atBottomRef.current = atBottom;
    setShowScrollBtn((shown) => shown !== !atBottom ? !atBottom : shown);
    if (!atBottom) userScrolledUpRef.current = true;
    if (atBottom) userScrolledUpRef.current = false;
  }, []);

  /**
   * Scroll only after React has committed a real message change. This executes
   * in the same paint cycle as the coalesced stream update and deliberately
   * has no competing requestAnimationFrame of its own.
   */
  const keepAtBottom = useCallback(() => {
    const el = chatRef.current;
    if (!el || userScrolledUpRef.current) return;
    el.scrollTop = el.scrollHeight;
    atBottomRef.current = true;
    setShowScrollBtn((shown) => shown ? false : shown);
  }, []);

  useLayoutEffect(() => {
    keepAtBottom();
  }, [keepAtBottom, visibleHistory]);

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


  // LLM health is probed by the backend using the real chat endpoint. A
  // one-shot request can legitimately race backend/provider warm-up after a
  // deploy, so retry only failed states with bounded backoff. Once connected,
  // this effect is quiet and does not create periodic provider traffic.
  useEffect(() => {
    let disposed = false;
    let retryDelay = LLM_HEALTH_RETRY_INITIAL_MS;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleRetry = () => {
      if (disposed || retryTimer) return;
      const delay = retryDelay;
      retryDelay = Math.min(retryDelay * 2, LLM_HEALTH_RETRY_MAX_MS);
      retryTimer = setTimeout(() => {
        retryTimer = null;
        void refreshLlmHealth();
      }, delay);
    };

    const refreshLlmHealth = async () => {
      try {
        const status = await settingsApi.llmStatus();
        if (disposed || !status) {
          if (!disposed) scheduleRetry();
          return;
        }
        const connected = Boolean(status.connected);
        setLlmHealth({
          connected,
          provider: status.provider || status.provider_type || "",
          model: status.model || "",
          recentFailure: status.recent_failure?.error_type
            ? status.recent_failure.error_summary
            : undefined,
          visionSupported: status.vision_supported,
        });
        if (connected) {
          retryDelay = LLM_HEALTH_RETRY_INITIAL_MS;
          return;
        }
      } catch {
        // Retain the last known state: a transient status request failure must
        // not overwrite a previously healthy indicator.
      }
      scheduleRetry();
    };

    void refreshLlmHealth();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, []);

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

  const { send: sendPrepared, stop: stopGeneration } = useWorkbenchSend({
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
  const onSend = useCallback((text?: string, metadata?: Record<string, unknown>) => {
    if (selectedSkill && selectedResourceIds.length === 0) {
      toast({ kind: "warning", title: "尚未选择资源", body: "当前 Skill 至少需要选择一项可用资源后才能执行。" });
      return Promise.resolve();
    }
    const workbenchSelection = selectedSkill ? {
      extension_id: selectedSkill.extension_id,
      skill_id: selectedSkill.skill_id,
      resource_ids: selectedResourceIds,
    } : undefined;
    return sendPrepared(text, { ...(metadata || {}), ...(workbenchSelection ? { workbench_selection: workbenchSelection } : {}) });
  }, [selectedResourceIds, selectedSkill, sendPrepared, toast]);

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
    void jobsApi.cancel(
      activeJob.job_id,
      currentWorkspaceId,
      activeJob.metadata?.active_turn?.client_request_id,
    )
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

  return (
    <div className={["wb-shell", progressPanelCollapsed ? "is-progress-collapsed" : "", headerCollapsed ? "is-header-collapsed" : ""].filter(Boolean).join(" ")}>
      <section className="wb-conversation-column">
        <header className="wb-header" id="workbench-session-header">
          <div className="wb-header-context">
            <span className="wb-header-kicker">{viewMode === "chat" ? "当前会话" : "运行记录"}</span>
            <h1 title={sessionTitle}>{viewMode === "chat" ? sessionTitle : "完整时间线"}</h1>
          </div>
          <div className="wb-header-actions">
            <button
              type="button"
              className="wb-header-collapse"
              aria-label={headerCollapsed ? "展开会话栏" : "收起会话栏"}
              aria-controls="workbench-session-header"
              aria-expanded={!headerCollapsed}
              onClick={() => setHeaderCollapsed((collapsed) => !collapsed)}
              data-testid="btn-toggle-session-header"
            >
              <IconChevronDown size={14} /><span>{headerCollapsed ? "展开" : "收起"}</span>
            </button>
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
          {currentSessionId && workbenchSkills.length > 0 ? (
            <div className="wb-skill-picker" data-testid="workbench-skill-picker">
              <label>
                <span>Skill</span>
                <select value={selectedSkillKey} onChange={(event) => {
                  const skillKey = event.target.value;
                  const skill = workbenchSkills.find((item) => `${item.extension_id}:${item.skill_id}` === skillKey);
                  setSelectedSkillKey(skillKey);
                  setSelectedResourceIds(skill?.default_resource_ids || []);
                }}>
                  <option value="">通用对话</option>
                  {workbenchSkills.map((skill) => <option key={`${skill.extension_id}:${skill.skill_id}`} value={`${skill.extension_id}:${skill.skill_id}`}>{skill.name}</option>)}
                </select>
              </label>
              {selectedSkill ? <div className="wb-skill-devices" aria-label="选择 Skill 资源">
                {selectedSkill.resources.map((resource) => {
                  const active = selectedResourceIds.includes(resource.resource_id);
                  return <button key={resource.resource_id} type="button" className={active ? "active" : ""} title={resource.description} onClick={() => setSelectedResourceIds((items) => active ? items.filter((item) => item !== resource.resource_id) : selectedSkill.selection_mode === "single" ? [resource.resource_id] : [...items, resource.resource_id])}>{resource.name}</button>;
                })}
              </div> : null}
            </div>
          ) : null}
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
              rows={1}
              data-testid="chat-input"
              spellCheck={false}
            />
            <div className="wb-composer-actions">
              <input ref={fileInputRef} type="file" multiple disabled={!currentSessionId || turnRunning} accept=".txt,.md,.json,.csv,.tsv,.log,.conf,.cfg,.yaml,.yml,.xml,.html,.htm,.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.gif,.webp" onChange={(event) => { if (event.target.files) { addFiles(event.target.files); event.target.value = ""; } }} className="wb-file-input" />
              <button className="wb-attach-btn" onClick={pickFile} disabled={!currentSessionId || turnRunning} title={currentSessionId ? "添加文件" : "请先新建会话"} aria-label={currentSessionId ? "添加文件" : "请先新建会话"} type="button">
                <IconAttachment size={16} aria-hidden="true" />
              </button>
              {turnRunning ? (
                <button className="wb-stop" onClick={stopActiveTurn} title="停止当前任务" aria-label="停止当前任务" type="button" data-testid="btn-stop">
                  <IconStop size={15} weight="fill" aria-hidden="true" />
                </button>
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
      <ApprovalBubble onResolved={refreshAfterApproval} />
    </div>
  );
}
