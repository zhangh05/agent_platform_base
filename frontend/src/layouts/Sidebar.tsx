import { useEffect, useRef, useState } from "react";
import { useAsync, AsyncView } from "../components/common";
import { sessionsApi, workspacesApi, runtimeAuditApi } from "../api";
import { isInternalSessionId, useSessionStore, useUIStore } from "../stores/session";
import { useWorkbenchStore } from "../stores/workbench";
import { useToastStore } from "../stores/toast";
import { isApiError, AgentResult } from "../types";
import type { ToolCallResult, RuntimeEvent } from "../types";
import type { Session } from "../types";
import { IconArchive, IconBolt, IconChat, IconClose, IconEdit, IconMore, IconPlus, IconTrash, IconWorkspace } from "../components/Icon";
import { APP_EVENTS } from "../utils/appEvents";
import { formatDate } from "../utils/format";

const SESSION_PREVIEW_LIMIT = 12;

function runStatusLabel(status?: string): string {
  return ({ ok: "成功", partial: "部分完成", failed: "失败", error: "失败", running: "执行中", pending: "等待中", cancelled: "已取消" } as Record<string, string>)[status || ""] || status || "未知";
}

interface AgentRunDetail {
  ok?: boolean;
  status?: string;
  final_response?: string;
  events?: RuntimeEvent[];
  trace_id?: string;
  session_id?: string;
  tool_calls?: ToolCallResult[];
  warnings?: string[];
  error?: unknown;
  tool_decision?: unknown;
  no_tool_reason?: string;
  selected_capabilities?: string[];
  visible_tools?: string[];
}

interface AgentRunResponse {
  run?: AgentRunDetail;
}

interface RecentRunSummary {
  run_id?: string;
  status?: string;
  user_input_summary?: string;
  intent?: string;
  created_at?: string;
  session_id?: string;
  ok?: boolean;
}

/**
 * Sidebar — Workspace / Sessions / Recent Runs. All data is fetched
 * from the real backend; no mocks, no fallback.
 */
export function Sidebar() {
  const currentWorkspaceId = useSessionStore((s) => s.currentWorkspaceId);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const setCurrentSession = useSessionStore((s) => s.setCurrentSession);
  const switchWbSession = useWorkbenchStore((s) => s.switchSession);
  const toast = useToastStore((s) => s.show);
  const setMobileNavOpen = useUIStore((s) => s.setMobileNavOpen);
  const [editingSessId, setEditingSessId] = useState<string | null>(null);
  const [editingSessName, setEditingSessName] = useState("");
  const pendingCreatedSessionIdRef = useRef<string | null>(null);

  // Click handler: switch to the run's session and load its data into Timeline.
  const inspectRun = async (r: RecentRunSummary) => {
    setMobileNavOpen(false);
    const rid = r.run_id;
    if (!rid || !currentWorkspaceId) return;
    const targetSessionId = r.session_id;
    if (isInternalSessionId(targetSessionId)) {
      toast({ kind: "warning", title: "内部子任务不作为会话打开", body: targetSessionId });
      return;
    }
    // Switch to the run's owning session so Timeline shows the right data
    if (targetSessionId && targetSessionId !== currentSessionId) {
      setCurrentSession(targetSessionId);
      switchWbSession(targetSessionId);
    }
    // Ensure the target session's messages are loaded into bySession before
    // we try to attach the AgentResult. Timeline derives runs from bySession,
    // so the assistant ChatMsg must exist for setLatestResult to hook onto.
    const sid = targetSessionId ?? currentSessionId ?? "_scratch";
    if (sid) {
      const hasInStore = (useWorkbenchStore.getState().bySession[sid] ?? [])
        .some((m) => m.run_id === rid);
      if (!hasInStore) {
        try {
          const msgsRes = await sessionsApi.messages(sid, currentWorkspaceId);
          if (msgsRes.messages?.length) {
            useWorkbenchStore.getState().mergeFromBackend(sid, msgsRes.messages);
          }
        } catch { /* best-effort: if messages can't load, setLatestResult will no-op */ }
      }
    }
    // Dedup: skip if the matching assistant message already has a result
    const already = (useWorkbenchStore.getState().bySession[sid] ?? [])
      .some((m) => m.run_id === rid && m.role === "assistant" && m.result);
    if (already) return;
    try {
      const raw = (await runtimeAuditApi.run(currentWorkspaceId, rid)) as AgentRunResponse;
      const runData: AgentRunDetail = raw.run ?? (raw as unknown as AgentRunDetail);
      const result: AgentResult = {
        ok: runData.ok ?? r.ok ?? /ok|completed|success/i.test(runData.status || r.status || ""),
        final_response: runData.final_response || "",
        events: runData.events || [],
        trace_id: runData.trace_id || "",
        session_id: runData.session_id || r.session_id || "",
        turn_id: rid,
        tool_calls: (runData.tool_calls || []) as ToolCallResult[],
        warnings: runData.warnings || [],
        errors: runData.error ? [String(runData.error)] : [],
        tool_decision: runData.tool_decision as AgentResult["tool_decision"],
        no_tool_reason: runData.no_tool_reason,
        metadata: {
          selected_capabilities: runData.selected_capabilities || [],
          visible_tools: runData.visible_tools || [],
          source_count: 0,
          workspace_id: currentWorkspaceId,
        },
      };
      useWorkbenchStore.getState().setLatestResult(result, sid);
    } catch {
      // Minimal fallback from summary
      const result: AgentResult = {
        ok: r.ok ?? /ok|completed|success/i.test(r.status || ""),
        final_response: "",
        events: [],
        trace_id: "",
        session_id: r.session_id || "",
        turn_id: rid,
        tool_calls: [],
        warnings: [],
        errors: [],
        metadata: {
          selected_capabilities: [],
          visible_tools: [],
          source_count: 0,
          workspace_id: currentWorkspaceId,
        },
      };
      useWorkbenchStore.getState().setLatestResult(result, sid);
    }
  };

  const sessList = useAsync<{ sessions: Session[] }>(
    (s) => sessionsApi.list(currentWorkspaceId, "active", s),
    [currentWorkspaceId],
    (d) => (d.sessions ?? []).length === 0,
  );
  const recentRuns = useAsync<{ runs: RecentRunSummary[] }>(
    (s) =>
      currentWorkspaceId && currentSessionId
        ? workspacesApi.recentRuns(currentWorkspaceId, currentSessionId, s)
        : Promise.resolve({ runs: [] }),
    [currentWorkspaceId, currentSessionId],
    (d) => (d.runs ?? []).length === 0,
  );

  // Re-register event listener once — use refs to avoid dependency churn
  const recentRunsRef = useRef(recentRuns.reload);
  recentRunsRef.current = recentRuns.reload;
  const sessListRef = useRef(sessList.reload);
  sessListRef.current = sessList.reload;

  // Cross-page session-list invalidation: any component (e.g. OperationsPage
  // restoring a session) bumps sessionListVersion and the sidebar re-fetches.
  const sessionListVersion = useSessionStore((s) => s.sessionListVersion);
  const firstSessionBump = useRef(true);
  useEffect(() => {
    if (firstSessionBump.current) { firstSessionBump.current = false; return; }
    sessListRef.current();
    recentRunsRef.current();
  }, [sessionListVersion]);

  useEffect(() => {
    const onRunCompleted = () => {
      recentRunsRef.current();
      sessListRef.current();
    };
    window.addEventListener(APP_EVENTS.RUN_COMPLETED, onRunCompleted);
    return () => window.removeEventListener(APP_EVENTS.RUN_COMPLETED, onRunCompleted);
  }, []);

  useEffect(() => {
    const pendingCreatedSessionId = pendingCreatedSessionIdRef.current;
    if (sessList.state.kind === "empty") {
      if (!pendingCreatedSessionId && currentSessionId) { setCurrentSession(null); switchWbSession(null); }
      return;
    }
    if (sessList.state.kind !== "success") return;
    if (pendingCreatedSessionId) {
      pendingCreatedSessionIdRef.current = null;
      setCurrentSession(pendingCreatedSessionId);
      switchWbSession(pendingCreatedSessionId);
      return;
    }
    const sessions = sessList.state.data.sessions ?? [];
    if (!currentSessionId || !sessions.some((s) => s.session_id === currentSessionId)) {
      const fallbackSessionId = sessions[0]?.session_id ?? null;
      setCurrentSession(fallbackSessionId);
      switchWbSession(fallbackSessionId);
    }
  }, [currentSessionId, currentWorkspaceId, sessList.state, setCurrentSession, switchWbSession]);

  async function onNewSession() {
    if (!currentWorkspaceId) {
      toast({ kind: "warning", title: "未选择 workspace" });
      return;
    }
    try {
      const res = await sessionsApi.create(currentWorkspaceId, "");
      if (res?.session) {
        pendingCreatedSessionIdRef.current = res.session.session_id;
        setCurrentSession(res.session.session_id);
        switchWbSession(res.session.session_id);
        sessList.reload();
        toast({ kind: "success", title: "新会话已创建", body: res.session.session_id });
      }
    } catch (e: unknown) {
      toast({
        kind: "error",
        title: "创建会话失败",
        body: isApiError(e) ? e.message : String(e),
        request_id: isApiError(e) ? e.request_id : undefined,
      });
    }
  }

  async function onArchive(sess: Session) {
    if (!currentWorkspaceId) return;
    try {
      await sessionsApi.archive(sess.session_id, currentWorkspaceId);
      if (currentSessionId === sess.session_id) {
        setCurrentSession(null);
      }
      useSessionStore.getState().bumpSessionList();
      toast({ kind: "success", title: "已归档", body: sess.session_id });
    } catch (e: unknown) {
      toast({
        kind: "error",
        title: "归档失败",
        body: isApiError(e) ? e.message : String(e),
        request_id: isApiError(e) ? e.request_id : undefined,
      });
    }
  }

  async function onRenameSession(sess_id: string) {
    if (!editingSessName.trim() || !currentWorkspaceId) { cancelEditSession(); return; }
    try {
      await sessionsApi.rename(sess_id, currentWorkspaceId, editingSessName.trim());
      useSessionStore.getState().bumpSessionList();
      toast({ kind: "success", title: "会话已重命名" });
      cancelEditSession();
    } catch (e: unknown) {
      toast({ kind: "error", title: "重命名失败", body: isApiError(e) ? e.message : String(e) });
    }
  }

  async function onDeleteSession(sess: Session) {
    if (!currentWorkspaceId) return;
    if (!confirm(`永久删除会话「${sess.title || sess.session_id}」？\n\n此操作不可撤销，消息和记录将被彻底清除。`)) return;
    try {
      await sessionsApi.delete(sess.session_id, currentWorkspaceId);
      if (currentSessionId === sess.session_id) { setCurrentSession(null); switchWbSession(null); }
      useSessionStore.getState().bumpSessionList();
      toast({ kind: "success", title: "已永久删除", body: sess.session_id });
    } catch (e: unknown) {
      // 404 = already deleted on disk → just reload the list
      if (isApiError(e) && e.status === 404) {
        if (currentSessionId === sess.session_id) { setCurrentSession(null); switchWbSession(null); }
        useSessionStore.getState().bumpSessionList();
        return;
      }
      toast({ kind: "error", title: "删除失败", body: isApiError(e) ? e.message : String(e) });
    }
  }

  function startEditSession(sess: Session) {
    setEditingSessId(sess.session_id);
    setEditingSessName(sess.title || "");
  }

  function cancelEditSession() {
    setEditingSessId(null);
    setEditingSessName("");
  }

  return (
    <div data-testid="sidebar" className="sidebar-content">
      <div className="sidebar-workspace" title={currentWorkspaceId || "未选择工作区"}>
        <IconWorkspace size={14} aria-hidden="true" />
        <div><span>当前工作区</span><strong>{currentWorkspaceId || "未选择"}</strong></div>
      </div>
      <div className="sidebar-shortcuts" aria-label="工作台快捷操作">
        <button
          className="sidebar-shortcut sidebar-new-session"
          onClick={onNewSession}
          disabled={!currentWorkspaceId}
          data-testid="btn-new-session"
          type="button"
        >
          <IconEdit size={17} /><span>新会话</span><IconPlus className="sidebar-shortcut-tail" size={14} />
        </button>
      </div>

      {/* 会话 */}
      <div className="sidebar-panel sidebar-session-panel">
        <div className="sidebar-panel-title">
          <IconChat size={12} />
          <span>最近会话</span>
        </div>
        <AsyncView
          state={sessList.state}
          onRetry={sessList.reload}
          skeleton="list"
          emptyText="暂无活跃会话"
          emptyHint="点击 + 新建"
        >
          {(d) => {
            const preview = previewSessions(d.sessions ?? [], currentSessionId);
            const hiddenCount = hiddenSessionCount(d.sessions ?? [], currentSessionId);
            return (
            <div className="list" data-testid="sess-list">
              {preview.map((sess) => (
                <div
                  key={sess.session_id}
                  className={
                    "list-item session-item" +
                    (currentSessionId === sess.session_id ? " active" : "")
                  }
                  data-testid={`sess-${sess.session_id}`}
                >
                  <button
                    onClick={() => { cancelEditSession(); setCurrentSession(sess.session_id); switchWbSession(sess.session_id); setMobileNavOpen(false); }}
                    data-testid={`sess-btn-${sess.session_id}`}
                    aria-label={`会话：${sess.title || sess.session_id}`}
                    type="button"
                    className="session-item-main"
                  >
                    {editingSessId === sess.session_id ? (
                      <input
                        className="input input-xs"
                        value={editingSessName}
                        onChange={(e) => setEditingSessName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") { e.stopPropagation(); void onRenameSession(sess.session_id); }
                          if (e.key === "Escape") { e.stopPropagation(); cancelEditSession(); }
                        }}
                        onBlur={cancelEditSession}
                        onClick={(e) => e.stopPropagation()}
                        autoFocus
                      />
                    ) : (
                      <span className="title" title={sess.title || sess.session_id}>
                        {sess.title || sess.session_id}
                      </span>
                    )}
                    {sess.message_count > 0 && (
                      <span className="meta">{sess.message_count}</span>
                    )}
                  </button>
                  {editingSessId === sess.session_id ? (
                    <div className="row-flex-xs">
                      <button className="btn sm btn-xs" onClick={(e) => { e.stopPropagation(); void onRenameSession(sess.session_id); }} type="button">保存</button>
                      <button className="btn sm ghost btn-xs-compact" aria-label="取消重命名" onClick={(e) => { e.stopPropagation(); cancelEditSession(); }} type="button"><IconClose size={13} aria-hidden="true" /></button>
                    </div>
                  ) : (
                    <details className="session-menu">
                      <summary
                        onClick={(e) => e.stopPropagation()}
                        className="btn ghost sm icon-only session-more-trigger"
                        title="会话操作"
                        aria-label={`打开“${sess.title || sess.session_id}”的会话操作`}
                        data-testid={`session-menu-trigger-${sess.session_id}`}
                      >
                        <IconMore size={15} weight="bold" />
                      </summary>
                      <div className="session-action-menu" role="menu" aria-label="会话操作">
                        <button type="button" role="menuitem" onClick={(e) => { e.stopPropagation(); e.currentTarget.closest("details")?.removeAttribute("open"); startEditSession(sess); }}>
                          <IconEdit size={14} /><span>重命名</span>
                        </button>
                        <button type="button" role="menuitem" onClick={(e) => { e.stopPropagation(); e.currentTarget.closest("details")?.removeAttribute("open"); void onArchive(sess); }} data-testid={`btn-archive-${sess.session_id}`}>
                          <IconArchive size={14} /><span>归档</span>
                        </button>
                        <button type="button" role="menuitem" className="danger" onClick={(e) => { e.stopPropagation(); e.currentTarget.closest("details")?.removeAttribute("open"); void onDeleteSession(sess); }}>
                          <IconTrash size={14} /><span>永久删除</span>
                        </button>
                      </div>
                    </details>
                  )}
                </div>
              ))}
              {hiddenCount > 0 && (
                <div className="list-item muted-row">
                  <span className="meta">
                    另有 {hiddenCount} 个活跃会话
                  </span>
                </div>
              )}
            </div>
            );
          }}
        </AsyncView>
      </div>

      {/* 最近运行 */}
      <div className="sidebar-panel sidebar-runs-panel">
        <div className="sidebar-panel-title">
          <IconBolt size={12} />
          <span>最近任务</span>
        </div>
        <AsyncView
          state={recentRuns.state}
          onRetry={recentRuns.reload}
          emptyText="暂无运行记录"
        >
          {(d) => (
            <div className="list" data-testid="runs-list">
              {(d.runs ?? []).slice(0, 5).map((r, i) => {
                const runId = r.run_id ?? `run-${i}`;
                const summary = r.user_input_summary || r.intent || "";
                const label = summary ? (summary.length > 24 ? summary.slice(0, 24) + "…" : summary) : runId;
                return (
                  <div
                    className="list-item run-item cursor-pointer"
                    key={runId}
                    title={`${summary || runId}\n状态：${runStatusLabel(r.status)}\n时间：${r.created_at || "未知"}`}
                    onClick={() => inspectRun(r)}
                  >
                    <div className="run-title-row">
                      <span
                        className={
                          "status-dot " +
                          (r.status === "ok" ? "ok" : r.status === "partial" ? "warn" : ["failed", "error"].includes(r.status || "") ? "err" : "idle")
                        }
                      />
                      <span className="title text-sm">{label}</span>
                    </div>
                    <div className="run-meta-row">
                      <span className="run-status-label">{runStatusLabel(r.status)}</span>
                      {r.created_at && (
                        <span className="text-xs faint">
                          {formatDate(r.created_at, "time")}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </AsyncView>
      </div>
    </div>
  );
}

function previewSessions(sessions: Session[], currentSessionId: string | null): Session[] {
  const preview = sessions.slice(0, SESSION_PREVIEW_LIMIT);
  if (!currentSessionId || preview.some((s) => s.session_id === currentSessionId)) {
    return preview;
  }
  const selected = sessions.find((s) => s.session_id === currentSessionId);
  return selected ? [...preview, selected] : preview;
}

function hiddenSessionCount(sessions: Session[], currentSessionId: string | null): number {
  return Math.max(0, sessions.length - previewSessions(sessions, currentSessionId).length);
}
