/**
 * OperationsPage — 运行与作业
 *
 * 单页展示作业全貌与运行细节：
 *   - 左栏：作业列表（工作区维度，含所有 job 类型 + 重试/恢复动作）
 *   - 右栏：选中作业 → 概要 / 运行记录 / 统计 三 tab
 *          运行记录中点击某 run → 内联展开 trace / 事件时间线 / 失败原因
 */
import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { useSearchParams } from "../../router";
import { jobsApi, workspacesApi, sessionExtApi, runtimeAuditApi } from "../../api";
import { useSessionStore } from "../../stores/session";
import { APP_EVENTS } from "../../utils/appEvents";
import { useToastStore } from "../../stores/toast";
import { Badge, StatusDot, EmptyState, LoadingState, CodeBlock } from "../../components/common";
import { PageHeader, DetailPanel, Button, FilterBar, Input, Select } from "../../components/ui";
import { IconRefresh, IconDocument, IconHistory, IconBolt, IconAlert, IconTrash } from "../../components/Icon";
import { TraceDetailPanel } from "../../components/TraceDetailPanel";
import { deriveRunTraceStats } from "../../utils/runTraceStats";
import { formatEventTime, formatEventDetail, formatEventLabel } from "../../utils/runEvent";
import { formatDate } from "../../utils/format";
import { formatCompactDate } from "../../utils/displayText";
import { isApiError } from "../../types";
import type { RuntimeAuditTurn } from "../../types";

/* ── Types ── */

type JobItem = {
  job_id: string;
  job_type: string;
  status: string;
  title?: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
  started_at?: string;
  finished_at?: string;
  workspace_id?: string;
  payload?: Record<string, unknown>;
  run_ids?: string[];
  progress?: { current: number; total: number; percent: number; message: string };
  error?: string;
};

const TERMINAL_DELETE_STATUSES = new Set([
  "succeeded", "completed", "success", "ok", "failed", "error", "cancelled", "archived",
]);

function canHardDelete(job: JobItem): boolean {
  return TERMINAL_DELETE_STATUSES.has(String(job.status || "").toLowerCase());
}

/** Extract session_id from nested payload */
function getSessionId(job: JobItem): string {
  return String(job.payload?.session_id ?? "");
}

/* ── Labels ── */

const JOB_TYPE_LABELS: Record<string, string> = {
  agent_run: "智能体对话",
  network_inspection: "网络巡检任务",
  export_report: "报告导出",
  knowledge_index: "知识索引",
  generic_agent_task: "通用任务",
};

const JOB_TYPE_ICONS: Record<string, typeof IconHistory> = {
  agent_run: IconHistory,
  network_inspection: IconBolt,
  export_report: IconBolt,
  knowledge_index: IconDocument,
};

const STATUS_META: Record<string, { kind: "ok" | "err" | "warn" | "muted"; label: string; dot: "ok" | "err" | "warn" | "idle" }> = {
  succeeded: { kind: "ok", label: "已完成", dot: "ok" },
  completed: { kind: "ok", label: "已完成", dot: "ok" },
  success: { kind: "ok", label: "成功", dot: "ok" },
  ok: { kind: "ok", label: "正常", dot: "ok" },
  failed: { kind: "err", label: "失败", dot: "err" },
  error: { kind: "err", label: "错误", dot: "err" },
  running: { kind: "warn", label: "进行中", dot: "warn" },
  pending: { kind: "warn", label: "等待中", dot: "warn" },
  queued: { kind: "warn", label: "排队中", dot: "warn" },
  created: { kind: "muted", label: "待启动", dot: "idle" },
  cancelled: { kind: "muted", label: "已取消", dot: "idle" },
  archived: { kind: "muted", label: "已归档", dot: "idle" },
};

/* ── Helpers ── */

function sMeta(s: string) { return STATUS_META[s] || { kind: "muted" as const, label: s || "未知", dot: "idle" as const }; }

function calcDuration(start?: string, end?: string): string | null {
  if (!start) return null;
  const t0 = new Date(start).getTime();
  const t1 = end ? new Date(end).getTime() : Date.now();
  if (!Number.isFinite(t0) || !Number.isFinite(t1)) return null;
  const secs = Math.round((t1 - t0) / 1000);
  if (!Number.isFinite(secs) || secs < 0) return null;
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

function runIdentity(run: Partial<RuntimeAuditTurn>): string {
  return String(run.run_id || run.turn_id || "");
}

function runMatchesJob(run: RuntimeAuditTurn, job: JobItem): boolean {
  const ids = new Set((job.run_ids || []).filter(Boolean));
  if (ids.size === 0) return true;
  return ids.has(runIdentity(run));
}

function mergeRuns(primary: RuntimeAuditTurn[], extras: RuntimeAuditTurn[]): RuntimeAuditTurn[] {
  const seen = new Set<string>();
  const merged: RuntimeAuditTurn[] = [];
  for (const run of [...primary, ...extras]) {
    const id = runIdentity(run);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    merged.push(run);
  }
  return merged;
}

/** Run-level status (ported from RunsPage: prefer r.ok truth). */
function effectiveStatus(run: RuntimeAuditTurn): string {
  if (run.ok === false) return "error";
  if (run.ok === true) return "ok";
  return run.status || "ok";
}
function sBadge(s: string): "ok" | "err" | "warn" | "muted" {
  if (/ok|completed|success/i.test(s)) return "ok";
  if (/fail|error/i.test(s)) return "err";
  if (/run|pending/i.test(s)) return "warn";
  return "muted";
}
function sDot(s: string): "ok" | "err" | "warn" | "idle" {
  if (/ok|completed|success/i.test(s)) return "ok";
  if (/fail|error/i.test(s)) return "err";
  if (/run|pending/i.test(s)) return "warn";
  return "idle";
}
function sLabel(s: string): string {
  const M: Record<string, string> = { ok: "成功", completed: "已完成", success: "成功", failed: "失败", error: "失败", running: "运行中", pending: "等待中", timeout: "超时", cancelled: "已取消" };
  return M[s] || s || "未知";
}

/* ── Component ── */

export function OperationsPage() {
  const { currentWorkspaceId } = useSessionStore();
  const toast = useToastStore((s) => s.show);
  const wsId = currentWorkspaceId;
  const [searchParams] = useSearchParams();

  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(() => new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [jobQuery, setJobQuery] = useState("");
  const [jobStatus, setJobStatus] = useState("all");

  const [selectedJob, setSelectedJob] = useState<JobItem | null>(null);
  const selectedJobRef = useRef(selectedJob);
  selectedJobRef.current = selectedJob;
  const [jobTab, setJobTab] = useState<"overview" | "stats" | "summary">("overview");

  const [runs, setRuns] = useState<RuntimeAuditTurn[] | null>(null);
  const [runsLoading, setRunsLoading] = useState(false);

  // Drilled-in run (inline trace replaces job detail in the right pane)
  const [selRun, setSelRun] = useState<RuntimeAuditTurn | null>(null);
  const [trace, setTrace] = useState<any[] | null>(null);
  const [runTab, setRunTab] = useState<"overview" | "events">("overview");

  const loadJobs = useCallback(async () => {
    if (!wsId) { setJobs([]); setLoading(false); return; }
    setLoading(true); setError(null);
    try {
      const data = await jobsApi.list(wsId);
      setJobs((data?.jobs ?? []) as JobItem[]);
    } catch (e: unknown) {
      setError(isApiError(e) ? e.message : String(e));
    }
    setLoading(false);
  }, [wsId]);

  // Silent refresh: updates jobs list without flashing the loading indicator.
  // Used by the polling timer so transient-job tracking doesn't disrupt the UI.
  const loadJobsSilently = useCallback(async () => {
    if (!wsId) return;
    try {
      const data = await jobsApi.list(wsId);
      setJobs((data?.jobs ?? []) as JobItem[]);
    } catch { /* keep last known state */ }
  }, [wsId]);
  const loadJobsSilentlyRef = useRef(loadJobsSilently);
  loadJobsSilentlyRef.current = loadJobsSilently;

  useEffect(() => { loadJobs(); }, [loadJobs]);

  useEffect(() => {
    if (!selectedJob) return;
    const fresh = jobs.find((job) => job.job_id === selectedJob.job_id);
    if (!fresh) {
      selectedJobRef.current = null;
      setSelectedJob(null);
      setRuns(null);
      setSelRun(null);
      setTrace(null);
      return;
    }
    if (fresh !== selectedJob) {
      selectedJobRef.current = fresh;
      setSelectedJob(fresh);
    }
  }, [jobs, selectedJob]);

  // Cross-page linkage: any session lifecycle change (archive / restore / delete / rename)
  // bumps sessionListVersion; refresh the jobs list so it stays in sync with the sidebar.
  const loadJobsRef = useRef(loadJobs);
  loadJobsRef.current = loadJobs;
  const sessionListVersion = useSessionStore((s) => s.sessionListVersion);
  const firstSessionBump = useRef(true);
  useEffect(() => {
    if (firstSessionBump.current) { firstSessionBump.current = false; return; }
    loadJobsRef.current();
  }, [sessionListVersion]);

  // Reload the selected job's runs WITHOUT collapsing the panel (selectJob toggles off
  // when called with the same job and no focusRunId). Kept in a ref so the run-completed
  // listener below always sees the latest selection.
  const jobRunsRequestRef = useRef(0);
  const runTraceRequestRef = useRef(0);
  const deepLinkRequestRef = useRef(0);

  const openRun = useCallback(async (run: RuntimeAuditTurn) => {
    const rid = runIdentity(run);
    if (!rid) return;
    const requestId = ++runTraceRequestRef.current;
    setSelRun(run); setTrace(null); setRunTab("overview");
    try {
      const d = await runtimeAuditApi.trace(wsId, rid);
      if (requestId === runTraceRequestRef.current) setTrace(d.events || []);
    }
    catch { if (requestId === runTraceRequestRef.current) setTrace(null); }
  }, [wsId]);

  const loadRunsForJob = useCallback(async (job: JobItem): Promise<RuntimeAuditTurn[]> => {
    const sid = getSessionId(job);
    const expectedRunIds = (job.run_ids || []).filter(Boolean);
    if (!sid && expectedRunIds.length === 0) return [];

    let bySession: RuntimeAuditTurn[] = [];
    if (sid) {
      const limit = Math.min(100, Math.max(20, expectedRunIds.length * 3));
      const d = await workspacesApi.recentRuns(wsId, sid, undefined, limit);
      bySession = ((d?.runs ?? []) as RuntimeAuditTurn[]).filter((run) => runMatchesJob(run, job));
    }

    const loadedIds = new Set(bySession.map(runIdentity).filter(Boolean));
    const missingIds = expectedRunIds.filter((id) => !loadedIds.has(id)).slice(0, 50);
    const fetched = await Promise.all(missingIds.map(async (runId) => {
      try {
        const raw = await runtimeAuditApi.run(wsId, runId);
        const rec = ((raw as { run?: RuntimeAuditTurn })?.run || raw) as RuntimeAuditTurn;
        return rec && runIdentity(rec) ? rec : null;
      } catch {
        return null;
      }
    }));

    return mergeRuns(bySession, fetched.filter(Boolean) as RuntimeAuditTurn[]);
  }, [wsId]);

  const reloadJobRuns = useCallback(async () => {
    const job = selectedJobRef.current;
    if (!job) return;
    const requestId = ++jobRunsRequestRef.current;
    setRunsLoading(true);
    try {
      const list = await loadRunsForJob(job);
      if (requestId === jobRunsRequestRef.current && selectedJobRef.current?.job_id === job.job_id) {
        setRuns(list);
      }
    } catch { /* keep last known runs */ }
    finally { if (requestId === jobRunsRequestRef.current) setRunsLoading(false); }
  }, [loadRunsForJob]);
  const reloadJobRunsRef = useRef(reloadJobRuns);
  reloadJobRunsRef.current = reloadJobRuns;

  // Live linkage: when an agent run completes anywhere, refresh the jobs list and the
  // open job's runs so statuses stay current — mirrors what Sidebar + RuntimeAudit already do.
  useEffect(() => {
    const onRunCompleted = () => {
      loadJobsRef.current();
      reloadJobRunsRef.current();
    };
    window.addEventListener(APP_EVENTS.RUN_COMPLETED, onRunCompleted);
    return () => window.removeEventListener(APP_EVENTS.RUN_COMPLETED, onRunCompleted);
  }, []);

  // Self-healing reactivity for jobs that complete outside the conversation flow.
  // These never emit RUN_COMPLETED, so we poll the job
  // list only while at least one job is in a transient state, then stop.
  const TRANSIENT_JOB_STATUS = new Set([
    "running", "pending", "queued", "in_progress",
    "executing", "scheduled", "dispatching", "waiting",
  ]);
  const hasTransientJob = useMemo(
    () => jobs.some((j) =>
      TRANSIENT_JOB_STATUS.has(String(j.status || "").toLowerCase())),
    [jobs],
  );
  useEffect(() => {
    if (!hasTransientJob || !wsId) return;
    const id = window.setInterval(() => loadJobsSilentlyRef.current(), 10_000);
    return () => window.clearInterval(id);
  }, [hasTransientJob, wsId]);

  /** Select a job and load its runs (if it backs a session). */
  const selectJob = useCallback(async (
    job: JobItem,
    focusRunId?: string | null,
    options: { toggle?: boolean } = {},
  ) => {
    const requestId = ++jobRunsRequestRef.current;
    ++runTraceRequestRef.current;
    if (options.toggle !== false && selectedJobRef.current?.job_id === job.job_id && !focusRunId) {
      selectedJobRef.current = null;
      setSelectedJob(null); setRuns(null); setSelRun(null); setTrace(null);
      return;
    }
    selectedJobRef.current = job;
    setSelectedJob(job);
    setJobTab("overview"); setRuns(null); setSelRun(null); setTrace(null);

    if (getSessionId(job) || (job.run_ids || []).length > 0) {
      setRunsLoading(true);
      try {
        const list = await loadRunsForJob(job);
        if (requestId !== jobRunsRequestRef.current || selectedJobRef.current?.job_id !== job.job_id) return;
        setRuns(list);
        if (focusRunId) {
          const target = list.find((r) => runIdentity(r) === focusRunId);
          if (target) openRun(target);
        }
      } catch {
        if (requestId === jobRunsRequestRef.current) setRuns([]);
      }
      finally { if (requestId === jobRunsRequestRef.current) setRunsLoading(false); }
    } else {
      setRuns([]);
    }
  }, [loadRunsForJob, openRun]);

  const backToJob = useCallback(() => { setSelRun(null); setTrace(null); }, []);

  // Deep-link: ?job=<id> and/or ?focus=<run_id>
  useEffect(() => {
    if (jobs.length === 0) return;
    const deepLinkRequestId = ++deepLinkRequestRef.current;
    const jobId = searchParams.get("job");
    const focus = searchParams.get("focus");
    if (jobId) {
      const j = jobs.find((x) => x.job_id === jobId);
      if (j) void selectJob(j, focus, { toggle: false });
      return;
    }
    if (focus) {
      // Best-effort: scan agent_run jobs' sessions for the run.
      const candidates = jobs.filter((j) => j.job_type === "agent_run").slice(0, 12);
      (async () => {
        for (const j of candidates) {
          if (deepLinkRequestId !== deepLinkRequestRef.current) return;
          const sid = getSessionId(j);
          if (!sid) continue;
          try {
            const d = await workspacesApi.recentRuns(wsId, sid);
            const list = ((d?.runs ?? []) as RuntimeAuditTurn[]).filter((run) => runMatchesJob(run, j));
            const target = list.find((r) => runIdentity(r) === focus);
            if (target && deepLinkRequestId === deepLinkRequestRef.current) {
              selectedJobRef.current = j;
              setSelectedJob(j); setJobTab("overview"); setRuns(list); await openRun(target); return;
            }
          } catch { /* keep scanning */ }
        }
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs, openRun, searchParams, selectJob, wsId]);

  // Aggregated stats — runCount 用实际加载的 runs 数量
  const stats = useMemo(() => {
    const runList = runs ?? [];
    const totalTools = runList.reduce((s, r) => s + (r.tool_call_count ?? 0), 0);
    const totalErrors = runList.reduce((s, r) => s + (r.error_count ?? 0), 0);
    return { runCount: runList.length, totalTools, totalErrors };
  }, [runs]);

  const duration = useMemo(
    () => selectedJob ? calcDuration(selectedJob.started_at, selectedJob.finished_at) : null,
    [selectedJob],
  );

  const visibleJobs = useMemo(() => {
    const query = jobQuery.trim().toLowerCase();
    return jobs.filter((job) => {
      const status = String(job.status || "").toLowerCase();
      const matchesStatus = jobStatus === "all"
        || (jobStatus === "active" && TRANSIENT_JOB_STATUS.has(status))
        || (jobStatus === "completed" && ["succeeded", "completed", "ok", "success"].includes(status))
        || (jobStatus === "failed" && ["failed", "error", "cancelled", "timeout"].includes(status));
      if (!matchesStatus) return false;
      if (!query) return true;
      return `${job.title || ""} ${job.job_id || ""} ${JOB_TYPE_LABELS[job.job_type] || job.job_type}`.toLowerCase().includes(query);
    });
  }, [jobQuery, jobStatus, jobs]);

  const selectableVisibleIds = useMemo(
    () => visibleJobs.filter(canHardDelete).map((job) => job.job_id),
    [visibleJobs],
  );
  const allVisibleTerminalSelected = selectableVisibleIds.length > 0
    && selectableVisibleIds.every((jobId) => selectedJobIds.has(jobId));

  const toggleJobSelection = (jobId: string) => {
    setSelectedJobIds((previous) => {
      const next = new Set(previous);
      if (next.has(jobId)) next.delete(jobId); else next.add(jobId);
      return next;
    });
  };
  const toggleVisibleTerminalSelection = () => {
    setSelectedJobIds((previous) => {
      const next = new Set(previous);
      if (allVisibleTerminalSelected) selectableVisibleIds.forEach((jobId) => next.delete(jobId));
      else selectableVisibleIds.forEach((jobId) => next.add(jobId));
      return next;
    });
  };

  // ── Cancel / Retry / Restore ──
  const handleRetry = async (job_id: string) => {
    try { await jobsApi.retry(job_id, wsId); toast({ kind: "success", title: "已重试" }); loadJobs(); }
    catch (e: unknown) { toast({ kind: "error", title: "重试失败", body: isApiError(e) ? e.message : String(e) }); }
  };
  const handleDelete = async (job: JobItem) => {
    if (!canHardDelete(job)) {
      toast({ kind: "error", title: "仅终态任务可删除", body: "请先取消或等待任务结束后再删除。" });
      return;
    }
    if (!window.confirm(`永久删除任务「${job.title || job.job_id}」及其事件和日志？此操作不可恢复。`)) return;
    try {
      await jobsApi.delete(job.job_id, wsId);
      if (selectedJob?.job_id === job.job_id) {
        setSelectedJob(null);
        setRuns(null);
        setSelRun(null);
      }
      toast({ kind: "success", title: "任务已删除" });
      await loadJobs();
    } catch (e: unknown) {
      toast({ kind: "error", title: "删除失败", body: isApiError(e) ? e.message : String(e) });
    }
  };
  const handleBatchDelete = async () => {
    const jobIds = [...selectedJobIds].sort();
    if (!jobIds.length) return;
    if (!window.confirm(`永久删除已选 ${jobIds.length} 条任务及其事件和日志？此操作不可恢复。`)) return;
    try {
      await jobsApi.deleteMany(jobIds, wsId);
      if (selectedJob && jobIds.includes(selectedJob.job_id)) {
        selectedJobRef.current = null;
        setSelectedJob(null); setRuns(null); setSelRun(null);
      }
      setSelectedJobIds(new Set());
      toast({ kind: "success", title: `已永久删除 ${jobIds.length} 条任务` });
      await loadJobs();
    } catch (e: unknown) {
      toast({ kind: "error", title: "批量删除失败", body: isApiError(e) ? e.message : String(e) });
    }
  };
  const handleRestore = async (job: JobItem) => {
    const sid = getSessionId(job);
    if (!sid) return;
    try { await sessionExtApi.restore(sid, wsId); useSessionStore.getState().bumpSessionList(); toast({ kind: "success", title: "会话已恢复" }); }
    catch (e: unknown) { toast({ kind: "error", title: "恢复失败", body: isApiError(e) ? e.message : String(e) }); }
  };
  const canRestore = (job: JobItem): boolean => {
    const sid = getSessionId(job);
    if (!sid) return false;
    return ["succeeded", "completed", "failed", "cancelled"].includes(job.status);
  };

  // ── Empty state ──
  if (!loading && !error && jobs.length === 0) {
    return (
      <div className="page operations-page">
        <OperationsPageHeader count={0} onRefresh={loadJobs} />
        <div className="page-body">
          <div className="job-bulk-actions job-bulk-actions-empty" aria-label="批量任务管理">
            <label className="job-select-all">
              <input type="checkbox" disabled aria-label="选择当前筛选结果中的全部可删除任务" />
              选择可删除项
            </label>
            <Button variant="danger-ghost" size="sm" disabled>
              <IconTrash size={13} />删除已选 (0)
            </Button>
          </div>
          <div className="hero">
            <div className="hero-mark operations-hero-mark">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                <line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
              </svg>
            </div>
            <h2 className="hero-title">暂无任务记录</h2>
            <p className="hero-sub">当前没有可选择的终态任务。发起任务后，可在此勾选并批量永久删除。</p>
          </div>
        </div>
      </div>
    );
  }

  // ── Main ──
  return (
    <div className="page operations-page">
      <OperationsPageHeader count={jobs.length} onRefresh={loadJobs} />

      <FilterBar className="operations-filterbar">
        <Input aria-label="搜索任务" placeholder="搜索任务名称、类型或 ID" value={jobQuery} onChange={(event) => setJobQuery(event.target.value)} />
        <Select aria-label="筛选任务状态" value={jobStatus} onChange={(event) => setJobStatus(event.target.value)}>
          <option value="all">全部状态</option>
          <option value="active">执行中</option>
          <option value="completed">已完成</option>
          <option value="failed">失败或取消</option>
        </Select>
        <span className="operations-filter-count">显示 {visibleJobs.length} / {jobs.length} 项</span>
      </FilterBar>

      {error && (
        <div className="operations-alert">
          <IconAlert size={16} aria-hidden="true" /> {error}
        </div>
      )}

      {loading ? (
        <div className="page-body"><LoadingState text="正在加载任务列表…" skeleton="list" /></div>
      ) : (
        <div className="split-shell operations-split">
          {/* ══════ 左侧 作业列表 ══════ */}
          <aside className="list-scroll jobs-list operations-pane-scroll">
            {selectableVisibleIds.length > 0 && (
              <div className="job-bulk-actions">
                <label className="job-select-all">
                  <input
                    type="checkbox"
                    checked={allVisibleTerminalSelected}
                    onChange={toggleVisibleTerminalSelection}
                    aria-label="选择当前筛选结果中的全部可删除任务"
                  />
                  选择可删除项
                </label>
                <Button variant="danger-ghost" size="sm" disabled={selectedJobIds.size === 0} onClick={() => void handleBatchDelete()}>
                  <IconTrash size={13} />删除已选 ({selectedJobIds.size})
                </Button>
              </div>
            )}
            {visibleJobs.map((job) => {
              const meta = sMeta(job.status);
              const active = selectedJob?.job_id === job.job_id;
              const TypeIcon = JOB_TYPE_ICONS[job.job_type] || IconBolt;
              const jobDuration = calcDuration(job.started_at, job.finished_at);
              return (
                <article
                  key={job.job_id}
                  role="button"
                  tabIndex={0}
                  className={`job-card ${active ? "selected" : ""}`}
                  onClick={() => void selectJob(job)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      void selectJob(job);
                    }
                  }}
                >
                  <div className="job-card-head">
                    {canHardDelete(job) && (
                      <input
                        type="checkbox"
                        checked={selectedJobIds.has(job.job_id)}
                        aria-label={`选择任务 ${job.title || job.job_id}`}
                        onClick={(event) => event.stopPropagation()}
                        onChange={() => toggleJobSelection(job.job_id)}
                      />
                    )}
                    <StatusDot status={meta.dot} />
                    <span className="job-card-title">{job.title || job.job_id?.slice(0, 12)}</span>
                    <Badge kind={meta.kind}>{meta.label}</Badge>
                  </div>
                  <div className="job-card-meta">
                    <span className="row-flex-xs">
                      <TypeIcon size={11} className="opacity-60" />
                      {JOB_TYPE_LABELS[job.job_type] || job.job_type}
                    </span>
                    {job.created_at && <span>{formatCompactDate(job.created_at)}</span>}
                    {jobDuration && <span>{jobDuration}</span>}
                  </div>
                  {job.run_ids && job.run_ids.length > 0 && (
                    <div className="job-card-meta">
                      <span className="row-flex-xs">
                        <IconHistory size={11} className="opacity-60" />
                        {job.run_ids.length} 轮对话
                      </span>
                    </div>
                  )}
                  {job.error && (
                    <div className="job-card-meta">
                      <span className="danger-text job-error-text">{job.error.slice(0, 60)}</span>
                    </div>
                  )}
                  <div className="job-card-actions">
                    {(job.status === "failed" || job.status === "error") && (
                      <Button size="sm" onClick={(e) => { e.stopPropagation(); handleRetry(job.job_id); }}>重试</Button>
                    )}
                    {canRestore(job) && (
                      <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); handleRestore(job); }}>恢复</Button>
                    )}
                    {canHardDelete(job) && (
                      <Button variant="danger-ghost" size="sm" title="永久删除任务" onClick={(e) => { e.stopPropagation(); void handleDelete(job); }}><IconTrash size={13} />删除</Button>
                    )}
                  </div>
                </article>
              );
            })}
            {visibleJobs.length === 0 && <div className="operations-list-empty">没有符合筛选条件的任务</div>}
          </aside>

          {/* ══════ 右侧 详情 / run trace ══════ */}
          {selRun ? (
            <RunTraceView
              run={selRun}
              trace={trace}
              tab={runTab}
              setTab={setRunTab}
              onBack={backToJob}
            />
          ) : (
            <JobDetail
              job={selectedJob}
              jobTab={jobTab}
              setJobTab={setJobTab}
              runs={runs}
              runsLoading={runsLoading}
              stats={stats}
              duration={duration}
              onOpenRun={openRun}
              onRetry={handleRetry}
              onRestore={handleRestore}
            />
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════
   Sub-components
   ═══════════════════════════════════════════════════════ */

function OperationsPageHeader({ count, onRefresh }: { count: number; onRefresh: () => void }) {
  return (
    <PageHeader
      title="任务中心"
      subtitle="查看任务进度、执行记录和详细过程"
    >
      {count > 0 && (
        <div className="status-pill"><span className="dot accent" />{count} 项</div>
      )}
      <Button size="sm" variant="ghost" onClick={onRefresh} title="刷新"><IconRefresh size={14} /></Button>
    </PageHeader>
  );
}

/* ── Job detail (right pane) ── */

function JobDetail({
  job, jobTab, setJobTab, runs, runsLoading, stats, duration,
  onOpenRun, onRetry, onRestore,
}: {
  job: JobItem | null;
  jobTab: "overview" | "stats" | "summary";
  setJobTab: (t: "overview" | "stats" | "summary") => void;
  runs: RuntimeAuditTurn[] | null;
  runsLoading: boolean;
  stats: { runCount: number; totalTools: number; totalErrors: number };
  duration: string | null;
  onOpenRun: (r: RuntimeAuditTurn) => void;
  onRetry: (id: string) => void;
  onRestore: (job: JobItem) => void;
}) {
  if (!job) {
    return (
      <DetailPanel
        empty={{
          text: "选择一项任务",
          hint: "点击左侧任务，查看执行记录、统计和任务概要。",
        }}
      />
    );
  }

  const meta = sMeta(job.status);
  const restoreAvailable = Boolean(getSessionId(job)) && ["succeeded", "completed", "failed", "cancelled"].includes(job.status);

  return (
    <div className="split-detail operations-pane-scroll operations-detail-body">
      <div className="operations-detail-header">
        <StatusDot status={meta.dot} />
        <h3 className="operations-detail-title">
          {job.title || job.job_id?.slice(0, 12)}
        </h3>
        <Badge kind={meta.kind}>{meta.label}</Badge>
        <Badge kind="muted">{JOB_TYPE_LABELS[job.job_type] || job.job_type}</Badge>
      </div>

      {(stats.runCount > 0 || duration || getSessionId(job)) && (
        <div className="operations-stat-bar">
          {getSessionId(job) && <QuickStat label="会话" value={getSessionId(job).slice(0, 12)} mono />}
          {stats.runCount > 0 && <QuickStat label="对话轮数" value={String(stats.runCount)} />}
          {duration && <QuickStat label="耗时" value={duration} />}
          {stats.totalTools > 0 && <QuickStat label="工具调用" value={String(stats.totalTools)} />}
          {stats.totalErrors > 0 && <QuickStat label="错误" value={String(stats.totalErrors)} danger />}
        </div>
      )}

      {((job.status === "failed" || job.status === "error") || restoreAvailable) && (
        <div className="operations-detail-actions">
          {(job.status === "failed" || job.status === "error") && (
            <Button size="sm" onClick={() => onRetry(job.job_id)}>重试</Button>
          )}
          {restoreAvailable && (
            <Button variant="ghost" size="sm" onClick={() => onRestore(job)}>恢复</Button>
          )}
        </div>
      )}

      <div className="tabs operations-tabs">
        <button className={"tab" + (jobTab === "overview" ? " active" : "")} onClick={() => setJobTab("overview")}>
          执行记录 {stats.runCount > 0 && <span className="opacity-60 ml-1">{stats.runCount}</span>}
        </button>
        <button className={"tab" + (jobTab === "stats" ? " active" : "")} onClick={() => setJobTab("stats")}>统计</button>
        <button className={"tab" + (jobTab === "summary" ? " active" : "")} onClick={() => setJobTab("summary")}>概要</button>
      </div>

      {jobTab === "overview" && (
        <TabRuns runs={runs} runsLoading={runsLoading} onOpenRun={onOpenRun} job={job} />
      )}
      {jobTab === "stats" && (
        <TabStats job={job} runs={runs} runsLoading={runsLoading} stats={stats} duration={duration} />
      )}
      {jobTab === "summary" && <TabSummary job={job} />}
    </div>
  );
}

function QuickStat({ label, value, mono, danger }: { label: string; value: string; mono?: boolean; danger?: boolean }) {
  return (
    <span className="row-flex-xs">
      <span className="faint text-xs">{label}</span>
      <span className={`quick-stat-value ${mono ? "mono" : ""} ${danger ? "quick-stat-value-danger" : ""}`}>{value}</span>
    </span>
  );
}

/* ── Tab: 运行记录 ── */

function TabRuns({ job, runs, runsLoading, onOpenRun }: { job: JobItem; runs: RuntimeAuditTurn[] | null; runsLoading: boolean; onOpenRun: (r: RuntimeAuditTurn) => void; }) {
  if (runsLoading) return <LoadingState text="正在加载执行记录…" />;
  if (!runs || runs.length === 0) {
    return <EmptyState text={getSessionId(job) || (job.run_ids || []).length > 0 ? "该任务暂无可展示的执行记录" : "这类任务不生成执行记录"} />;
  }
  return (
    <div>
      <div className="section-head mb-2">共 {runs.length} 轮对话</div>
      {runs.map((r, i) => (
        <button
          key={r.run_id || r.turn_id || i}
          type="button"
          onClick={() => onOpenRun(r)}
          className="run-card"
        >
          <span className="run-card-index">{runs.length - i}</span>
          <span className="run-card-title">
            {r.user_input_summary || r.intent || "(无摘要)"}
          </span>
          <Badge kind={sBadge(effectiveStatus(r))}>{sLabel(effectiveStatus(r))}</Badge>
          {(r.tool_call_count ?? 0) > 0 && (
            <span className="run-card-meta">{r.tool_call_count} 工具</span>
          )}
          {r.created_at && (
            <span className="run-card-meta">{formatCompactDate(r.created_at)}</span>
          )}
          <svg className="run-card-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      ))}
    </div>
  );
}

/* ── Tab: 统计 ── */

function TabStats({ job, runs, runsLoading, stats, duration }: {
  job: JobItem; runs: RuntimeAuditTurn[] | null; runsLoading: boolean;
  stats: { runCount: number; totalTools: number; totalErrors: number }; duration: string | null;
}) {
  const runList = runs ?? [];
  const succeededRuns = runList.filter((r) => /ok|completed|success/i.test(r.status || "")).length;
  const failedRuns = runList.filter((r) => /fail|error/i.test(r.status || "")).length;
  const toolDist = runList.map((r) => r.tool_call_count ?? 0).filter((c) => c > 0);
  const maxTools = toolDist.length > 0 ? Math.max(...toolDist) : 0;
  const avgTools = toolDist.length > 0 ? (toolDist.reduce((a, b) => a + b, 0) / toolDist.length).toFixed(1) : "0";

  return (
    <div>
      <div className="stat-grid-4">
        <StatCard label="会话 ID" value={getSessionId(job).slice(0, 16) || "-"} mono />
        <StatCard label="任务 ID" value={job.job_id.slice(0, 12)} mono />
        <StatCard label="类型" value={JOB_TYPE_LABELS[job.job_type] || job.job_type} />
        <StatCard label="状态" value={sMeta(job.status).label} />
        <StatCard label="耗时" value={duration || "-"} />
      </div>

      <div className="section-head">执行统计</div>
      <div className="stat-grid-6">
        <StatCard label="总轮数" value={String(stats.runCount)} highlight />
        <StatCard label="成功" value={String(succeededRuns)} highlight ok />
        <StatCard label="失败" value={String(failedRuns)} highlight danger={failedRuns > 0} />
        <StatCard label="工具调用" value={String(stats.totalTools)} />
        <StatCard label="单轮最多" value={String(maxTools)} />
        <StatCard label="单轮平均" value={avgTools} />
      </div>

      <div className="section-head mb-2">时间线</div>
      <div className="row-flex-sm timeline-meta-row">
        <div>创建：{formatCompactDate(job.created_at) || "-"}</div>
        <div>开始：{formatCompactDate(job.started_at) || "-"}</div>
        <div>结束：{formatCompactDate(job.finished_at) || "-"}</div>
        <div>更新：{formatCompactDate(job.updated_at) || "-"}</div>
      </div>

      {runList.length > 0 && (
        <>
          <div className="section-head">各轮耗时</div>
          {runList.map((r, i) => (
            <div key={i} className="timeline-row">
              <span className="timeline-index">#{runList.length - i}</span>
              <span className="timeline-title">{r.user_input_summary || r.intent || "(无摘要)"}</span>
              <Badge kind={sBadge(effectiveStatus(r))}>{sLabel(effectiveStatus(r))}</Badge>
              {r.tool_call_count ? <span>{r.tool_call_count} 工具</span> : null}
              <span>{r.created_at ? formatCompactDate(r.created_at) : "-"}</span>
            </div>
          ))}
        </>
      )}
      {runsLoading && <LoadingState text="加载运行数据…" />}
    </div>
  );
}

/* ── Tab: 概要 ── */

function TabSummary({ job }: { job: JobItem }) {
  const rows: Array<[string, string]> = [
    ["任务 ID", job.job_id],
    ["类型", JOB_TYPE_LABELS[job.job_type] || job.job_type],
    ["状态", sMeta(job.status).label],
    ["会话 ID", getSessionId(job) || "—"],
    ["工作区", job.workspace_id || "—"],
    ["创建", formatCompactDate(job.created_at) || "—"],
    ["开始", formatCompactDate(job.started_at) || "—"],
    ["结束", formatCompactDate(job.finished_at) || "—"],
    ["更新", formatCompactDate(job.updated_at) || "—"],
  ];
  if (job.progress) rows.push(["进度", `${job.progress.current}/${job.progress.total} (${job.progress.percent}%) · ${job.progress.message || ""}`]);
  if (job.error) rows.push(["错误", job.error]);

  return (
    <div className="card">
      <div className="fragment-grid">
        {rows.map(([k, v]) => (
          <FragmentRow key={k} label={k} value={v} danger={k === "错误"} />
        ))}
      </div>
    </div>
  );
}

function FragmentRow({ label, value, danger }: { label: string; value: string; danger?: boolean }) {
  return (
    <>
      <span className="fragment-row-label">{label}</span>
      <span className={"fragment-row-value" + (danger ? " danger" : "")}>{value}</span>
    </>
  );
}

function StatCard({ label, value, mono, highlight, ok, danger }: {
  label: string; value: string; mono?: boolean; highlight?: boolean; ok?: boolean; danger?: boolean;
}) {
  return (
    <div className="stat-card-box">
      <div className="stat-card-box-label">{label}</div>
      <div className={
        "stat-card-box-value" +
        (highlight ? " highlight" : "") +
        (ok ? " ok" : "") +
        (danger ? " danger" : "") +
        (mono ? " mono" : "")
      }>{value}</div>
    </div>
  );
}

/* ── Run trace (right pane, drilled in) ── */

function RunTraceView({ run, trace, tab, setTab, onBack }: {
  run: RuntimeAuditTurn;
  trace: any[] | null;
  tab: "overview" | "events";
  setTab: (t: "overview" | "events") => void;
  onBack: () => void;
}) {
  const failInfo = useMemo(() => {
    if (!trace) return null;
    const fv = trace.find((e: any) => e.event_type === "turn_failed" || e.type === "turn_failed");
    if (!fv) return null;
    const d = formatEventDetail(fv);
    const err = d.error || fv?.summary || String(d);
    let secs: number | null = null;
    const mr = trace.find((e: any) => /model.req/i.test(e.type || e.event_type || ""));
    const mp = trace.find((e: any) => /model.resp/i.test(e.type || e.event_type || ""));
    if (mr && mp) {
      const t0 = new Date(formatEventTime(mr)).getTime();
      const t1 = new Date(formatEventTime(mp)).getTime();
      if (t0 && t1) secs = Math.round((t1 - t0) / 1000);
    }
    return { error: String(err).slice(0, 200), timeoutSecs: secs };
  }, [trace]);

  const selectedStats = useMemo(() => deriveRunTraceStats(run, trace), [run, trace]);

  return (
    <div className="split-detail operations-pane-scroll operations-detail-body">
      <Button size="sm" variant="ghost" onClick={onBack} className="mb-3">
        ← 返回任务列表
      </Button>

      <div className="operations-detail-header">
        <StatusDot status={sDot(effectiveStatus(run))} />
        <h3 className="operations-detail-title">运行详情</h3>
        <Badge kind={sBadge(effectiveStatus(run))}>{sLabel(effectiveStatus(run))}</Badge>
      </div>

      <div className="tabs operations-tabs">
        <button className={"tab" + (tab === "overview" ? " active" : "")} onClick={() => setTab("overview")}>概览</button>
        <button className={"tab" + (tab === "events" ? " active" : "")} onClick={() => setTab("events")}>处理过程</button>
      </div>

      {tab === "overview" && (
        <>
          <div className="card">
            <div className="info-grid-4">
              <span className="info-grid-label">执行记录 ID</span>
              <span className="info-grid-value mono">{run.turn_id || run.run_id || "—"}</span>
              <span className="info-grid-label">过程记录 ID</span>
              <span className="info-grid-value mono">{run.trace_id || "—"}</span>
              <span className="info-grid-label">意图</span>
              <span className="info-grid-value">{run.intent || "—"}</span>
              <span className="info-grid-label">能力</span>
              <span className="info-grid-value">{run.capability || "—"}</span>
              <span className="info-grid-label">开始</span>
              <span className="info-grid-value">{formatDate(selectedStats.startedAt, "compact") || "—"}</span>
              <span className="info-grid-label">结束</span>
              <span className="info-grid-value">{formatDate(selectedStats.finishedAt, "compact") || "—"}</span>
              <span className="info-grid-label">工具调用</span>
              <span className="info-grid-value">{selectedStats.toolCallCount || 0}</span>
              <span className="info-grid-label">状态</span>
              <span>{run.ok ? <Badge kind="ok" withDot>成功</Badge> : <Badge kind="err" withDot>失败</Badge>}</span>
            </div>
          </div>

          <TraceDetailPanel traceEvents={trace} selectedRun={run} />

          {(!trace || trace.length === 0) && !selectedStats.toolCallCount && (
            <div className="operations-empty">暂无更多详情</div>
          )}
        </>
      )}

      {tab === "events" && (
        <>
          {!trace ? <LoadingState text="加载事件…" /> : (
            <>
              {failInfo && (
                <div className="card card-danger-left">
                  <div className="fail-reason-head">
                    <IconAlert size={14} /> 失败原因
                    {failInfo.timeoutSecs != null && <span className="fail-reason-timeout">· 耗时 {failInfo.timeoutSecs}s</span>}
                  </div>
                  <div className="fail-reason-body">{failInfo.error}</div>
                </div>
              )}
              <div className="section-head">处理过程 · {trace.length} 条记录</div>
              {trace.length === 0 ? <EmptyState text="该运行无事件记录" /> : (
                trace.map((ev: any, i: number) => {
                  const et = ev.event_type || ev.type || "unknown";
                  const dt = formatEventDetail(ev);
                  const lb = formatEventLabel(ev);
                  const isFail = et === "turn_failed";
                  return (
                    <div key={ev.event_id || i} className="card event-card">
                      <div className="event-card-head">
                        <div className="event-card-title">
                          <span className={"event-card-dot" + (isFail ? " danger" : " ok")} />
                          <span className="event-card-label">{lb}</span>
                        </div>
                        <span className="event-card-time">{formatEventTime(ev)}</span>
                      </div>
                      <details className="event-card-detail">
                        <summary>开发诊断 · {et}</summary>
                        <CodeBlock language="json">{JSON.stringify(dt, null, 2)}</CodeBlock>
                      </details>
                    </div>
                  );
                })
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
