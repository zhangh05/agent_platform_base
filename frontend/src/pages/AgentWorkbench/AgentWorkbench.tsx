import React, { lazy, Suspense, useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from "react";
import { jobsApi, sessionsApi, settingsApi } from "../../api";
import { apiRequest } from "../../api/client";
import { useSessionStore } from "../../stores/session";
import { useWorkbenchStore, type ChatMsg } from "../../stores/workbench";
import { useToastStore } from "../../stores/toast";
import { humanFailure } from "../../utils/humanizeError";
import "./WorkbenchHighlight";
import { IconAlert, IconChevronDown, IconRefresh } from "../../components/Icon";
import "../../components/RuntimeEventTimeline.css";
import { formatFileSize } from "../../utils/format";
import { MessageRow } from "./components/MessageRow";
import { scopedLocalStorageKey } from "../../utils/userScope";
import { useWorkbenchSend, type PendingAttachment } from "../../hooks/useWorkbenchSend";
import { useActiveTurn } from "../../hooks/useActiveTurn";
import { TaskProgressPanel } from "./components/TaskProgressPanel";
import { WorkbenchHeader } from "./components/WorkbenchHeader";
import { WorkbenchComposer, type WorkbenchSkill } from "./components/WorkbenchComposer";
import { WorkbenchEmptyState } from "./components/WorkbenchEmptyState";

const RuntimeEventTimeline = lazy(() => import("../../components/RuntimeEventTimeline").then((m) => ({ default: m.RuntimeEventTimeline })));

/* ── View mode ── */
type ViewMode = "chat" | "timeline";

interface WorkbenchAutoPrompt {
  prompt?: string;
  metadata?: Record<string, unknown>;
}

const EMPTY_CHAT_MESSAGES: ChatMsg[] = [];

/* ── timing constants ── */
const AUTO_SEND_DELAY_MS = 500;
const LLM_HEALTH_RETRY_INITIAL_MS = 1000;
const LLM_HEALTH_RETRY_MAX_MS = 30_000;
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
  const visibleHistory = useWorkbenchStore(
    (s) => s.bySession?.[currentSessionId ?? "_scratch"] ?? EMPTY_CHAT_MESSAGES,
  );
  const switchSession = useWorkbenchStore((s) => s.switchSession);
  const mergeFromBackend = useWorkbenchStore((s) => s.mergeFromBackend);

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
    const normalized = selectedResourceIds.filter((item) => available.has(item));
    if (normalized.length !== selectedResourceIds.length || normalized.some((item, index) => item !== selectedResourceIds[index])) {
      setSelectedResourceIds(normalized);
    }
  }, [selectedResourceIds, selectedSkill, selectedSkillKey, skillCatalogLoaded]);

  const [viewMode, setViewMode] = useState<ViewMode>("chat");
  const [progressPanelCollapsed, setProgressPanelCollapsed] = useState(false);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [headerCollapsed, setHeaderCollapsed] = useState(false);

  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const userScrolledUpRef = useRef(false);
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
  const { job: activeJob, loaded: activeTurnLoaded, refresh: refreshActiveTurn } = useActiveTurn(currentWorkspaceId, currentSessionId, sending);
  const durableTurn = activeJob?.metadata?.active_turn;
  const turnRunning = sending || activeJob?.status === "running";

  const pendingAutoMetadataRef = useRef<Record<string, unknown> | null>(null);
  const onSendRef = useRef<(text?: string, metadata?: Record<string, unknown>) => void>(() => {});

  const handleRetryOriginal = useCallback((text: string) => {
    onSendRef.current(text);
  }, []);

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
        // Retain previous state
      }
      scheduleRetry();
    };

    void refreshLlmHealth();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, []);

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
      skill_name: selectedSkill.name,
      resource_ids: selectedResourceIds,
    } : undefined;
    return sendPrepared(text, { ...(metadata || {}), ...(workbenchSelection ? { workbench_selection: workbenchSelection } : {}) });
  }, [selectedResourceIds, selectedSkill, sendPrepared, toast]);

  const latestAssistant = [...visibleHistory].reverse().find((message) => message.role === "assistant");

  useEffect(() => {
    if (!activeTurnLoaded || sending || !currentSessionId || !activeJob?.job_id || activeJob.status === "running") return;
    // A local streaming placeholder is never authoritative after a successful
    // durable-job read. This happens when a browser retained a WebSocket view
    // across a backend restart or an interrupted turn finished before the
    // terminal frame reached the page. A known non-running job (including an
    // unknown/failed terminal state) must not leave a fake running timer.
    for (const message of visibleHistory) {
      if (message.role !== "assistant" || message.status !== "streaming" || !message.activeJobId) continue;
      useWorkbenchStore.getState().updateAssistant(message.id, {
        status: "error",
        progressText: "",
        stageElapsedMs: undefined,
        error: "服务端确认该回合已不在运行队列；页面已停止等待。请查看任务记录或重新发起请求。",
        text: message.text || "本轮未收到服务端完成结果，已停止本地等待。",
      }, currentSessionId);
    }
  }, [activeJob?.job_id, activeJob?.status, activeTurnLoaded, currentSessionId, sending, visibleHistory]);

  useEffect(() => {
    if (!currentSessionId || activeJob?.status !== "running") return;
    // Repair a browser-local stale-state marker if a delayed Job snapshot
    // proves the same durable turn is still alive.
    for (const message of visibleHistory) {
      if (message.role !== "assistant" || message.status !== "error") continue;
      if (message.error !== "服务端确认该回合已不在运行队列；页面已停止等待。请查看任务记录或重新发起请求。") continue;
      useWorkbenchStore.getState().updateAssistant(message.id, {
        status: "streaming",
        error: "",
        text: message.text || "服务器任务仍在运行，页面已重新接入实时状态。",
      }, currentSessionId);
    }
  }, [activeJob?.status, currentSessionId, visibleHistory]);

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

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  }, [input]);

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
  }, [currentSessionId, currentWorkspaceId]);

  useEffect(() => {
    switchSession(currentSessionId);
    if (!currentSessionId || !currentWorkspaceId) return;
    const ctrl = new AbortController();
    sessionsApi.messages(currentSessionId, currentWorkspaceId, ctrl.signal)
      .then((res) => { if (res.messages?.length) mergeFromBackend(currentSessionId, res.messages); })
      .catch(() => {});
    return () => ctrl.abort();
  }, [currentSessionId, currentWorkspaceId, mergeFromBackend, switchSession]);

  useEffect(() => {
    onSendRef.current = onSend;
  }, [onSend]);

  function pickChip(prompt: string) {
    if (!currentSessionId) return;
    setInput(prompt);
    requestAnimationFrame(() => inputRef.current?.focus());
  }

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

  const handleDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); }, []);
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (!currentSessionId) return;
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  }, [currentSessionId]);

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

  return (
    <div className={["wb-shell", progressPanelCollapsed ? "is-progress-collapsed" : "", headerCollapsed ? "is-header-collapsed" : ""].filter(Boolean).join(" ")}>
      <section className="wb-conversation-column">
        <WorkbenchHeader
          sessionTitle={sessionTitle}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          headerCollapsed={headerCollapsed}
          onToggleHeaderCollapsed={() => setHeaderCollapsed((collapsed) => !collapsed)}
          llmHealth={llmHealth}
          currentSessionId={currentSessionId}
          visibleHistory={visibleHistory}
        />

        <div className="wb-chat" data-testid="chat-stream">
          {viewMode === "timeline" ? (
            <Suspense fallback={<div className="wb-timeline-loading" role="status">正在加载时间线…</div>}>
              <RuntimeEventTimeline messages={visibleHistory} />
            </Suspense>
          ) : visibleHistory.length === 0 && !turnRunning ? (
            <WorkbenchEmptyState
              currentSessionId={currentSessionId}
              onPickChip={pickChip}
            />
          ) : (
            <div ref={chatRef} className="wb-chat-list" role="log" aria-live={turnRunning ? "polite" : "off"} onScroll={handleChatScroll}>
              {visibleHistory.map((message, index) => (
                <MessageRow
                  key={message.message_id || message.id}
                  m={message}
                  idx={index}
                  total={visibleHistory.length}
                  lastUserInput={lastUserInput}
                  onRetryOriginal={handleRetryOriginal}
                />
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
                <button type="button" onClick={() => onSendRef.current(lastUserInput)} data-testid="retry-btn">
                  <IconRefresh size={13} />重试
                </button>
              ) : null}
            </div>
          );
        })()}

        <WorkbenchComposer
          currentSessionId={currentSessionId}
          turnRunning={turnRunning}
          input={input}
          onInputChange={handleInputChange}
          onSend={() => onSend()}
          onStop={stopActiveTurn}
          attachments={attachments}
          onRemoveAttachment={removeAttachment}
          onPickFile={pickFile}
          fileInputRef={fileInputRef}
          onFileInputChange={(e) => {
            if (e.target.files) {
              addFiles(e.target.files);
              e.target.value = "";
            }
          }}
          inputRef={inputRef}
          workbenchSkills={workbenchSkills}
          selectedSkillKey={selectedSkillKey}
          onSelectSkillKey={(key) => {
            const skill = workbenchSkills.find((item) => `${item.extension_id}:${item.skill_id}` === key);
            setSelectedSkillKey(key);
            setSelectedResourceIds(skill?.default_resource_ids || []);
          }}
          selectedSkill={selectedSkill}
          selectedResourceIds={selectedResourceIds}
          onSelectResourceIds={setSelectedResourceIds}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
        />
      </section>

      <TaskProgressPanel
        latestAssistant={progressAssistant}
        snapshot={durableTurn}
        turnRunning={turnRunning}
        onShowTimeline={handleShowTimeline}
        collapsed={progressPanelCollapsed}
        onToggleCollapsed={handleToggleProgressPanel}
      />
    </div>
  );
}
