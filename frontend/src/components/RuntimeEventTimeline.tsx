/**
 * RuntimeEventTimeline — collapsible run cards derived from ChatMsg[].
 *
 * C-plan: Timeline no longer reads a parallel `results` array. Instead it
 * groups messages by `run_id` (one user + one assistant per run) and renders
 * each pair as a card. If the assistant message has an attached
 * `AgentResult`, the expanded view shows the full event timeline; otherwise
 * On first expand, fires `loadRunDetail(workspace_id, run_id)` to
 * fetch /api/runs/<id> + /api/runs/<id>/trace and attach the merged result
 * to the assistant message. While loading we show a spinner; on error we
 * fall back to the user/assistant text pair with a retry hint.
 */
import React, { useMemo, useState } from "react";
import type { AgentResult, RuntimeEvent, ToolCallResult } from "../types";
import type { ChatMsg } from "../stores/workbench";
import { useWorkbenchStore } from "../stores/workbench";
import { useSessionStore } from "../stores/session";
import { shortId } from "../utils/displayText";

/* ── helpers ── */

function formatMs(ms?: number | null): string {
  if (ms == null) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
function toolLabel(s: string): string {
  const p = s.split(".");
  return p.length > 1 ? p[p.length - 1] : s;
}
function timeStr(evt: RuntimeEvent): string {
  const s = evt.occurred_at || evt.started_at || "";
  return s.slice(11, 19) || s.slice(0, 10);
}

/* ── step label & colour ── */

function stepLabel(evt: RuntimeEvent): string {
  return evt.name || evt.event_type || evt.type || "步骤";
}
function stepColorClass(evt: RuntimeEvent): string {
  const t = (evt.event_type || evt.type || "").toLowerCase();
  if (t.includes("error")) return "danger";
  if (t.includes("retry")) return "accent";
  if (t.includes("warn"))  return "warn";
  if (t.includes("tool"))  return "warn";
  if (t.includes("model")) return "accent";
  if (t.includes("final") || t.includes("response")) return "accent";
  if (t.includes("complete") || t.includes("ok")) return "ok";
  return "muted";
}

/* ── tiny tool chip ── */

const ToolChip: React.FC<{ tc: ToolCallResult }> = React.memo(({ tc }) => {
  const [open, setOpen] = useState(false);
  const hasBody = !!(tc.summary || tc.errors?.length || tc.artifacts?.length);
  return (
    <div className="rt-step rt-step-tool">
      <span className="rt-dot rt-dot-warn" />
      <div className="rt-step-body">
        <div className={`rt-step-head ${hasBody ? "rt-step-head--interactive" : ""}`} onClick={() => hasBody && setOpen(!open)}>
          <span className="rt-step-ok">{tc.ok ? "✓" : "✗"}</span>
          <code className="rt-step-name">{toolLabel(tc.tool_id)}</code>
          <span className="rt-tag">{tc.ok ? "完成" : "失败"}</span>
          {tc.duration_ms != null && <span className="rt-dur">{formatMs(tc.duration_ms)}</span>}
          {hasBody && <span className="rt-chev">{open ? "▲" : "▼"}</span>}
        </div>
        {open && tc.summary && <div className="rt-step-detail">{tc.summary}</div>}
        {open && tc.errors?.map((e, i) => <div key={`${tc.call_id}-err-${i}-${e.slice(0, 32)}`} className="rt-step-err">{e}</div>)}
        {open && tc.artifacts?.length ? (
          <div className="rt-chips">
            {tc.artifacts.map((a) => <span key={a.artifact_id} className="rt-chip">{a.title || a.artifact_id.slice(0, 8)}</span>)}
          </div>
        ) : null}
      </div>
    </div>
  );
});

/* ── step row ── */

const StepRow: React.FC<{ evt: RuntimeEvent }> = React.memo(({ evt }) => {
  const colorClass = stepColorClass(evt);
  const label = stepLabel(evt);
  const msg = evt.summary || evt.message || evt.error || "";
  return (
    <div className="rt-step">
      <span className={`rt-dot rt-dot-${colorClass}`} />
      <div className="rt-step-body">
        <div className="rt-step-line">
          <span className="rt-step-label">{label}</span>
          <span className="rt-step-ts">{timeStr(evt)}</span>
        </div>
        {msg && <div className="rt-step-msg">{msg}</div>}
      </div>
    </div>
  );
});

/* ── result-driven body (assistant has AgentResult attached) ── */

const ResultBody: React.FC<{ result: AgentResult }> = React.memo(({ result }) => {
  const events = result.events ?? [];
  const tools = result.tool_calls ?? [];
  const hasDiag = !!(result.errors?.length) || !!(result.warnings?.length);
  const allArtifacts = tools.flatMap((t) => t.artifacts ?? []);
  const retrySummary = result.metadata?.retry_summary || {};
  const retryEvents = result.metadata?.retry_events || [];
  const retryAttempts = Number(retrySummary.retry_attempts || 0);
  const validationCorrectionSummary = result.metadata?.validation_correction_summary || {};
  const validationCorrectionAttempts = Number(validationCorrectionSummary.attempts || 0);
  const toolRecoveryEvents = result.metadata?.tool_recovery_events || [];
  const trackingSummary = (result.metadata?.tracking_summary || {}) as Record<string, any>;
  const trackingTaskId = String(trackingSummary.task_id || "");
  const trackingStatus = String(trackingSummary.status || "");
  const trackingDomain = String(trackingSummary.domain || "");

  // Merge tool calls into events
  const toolMap = new Map<string, ToolCallResult>();
  for (const tc of tools) { if (tc.call_id) toolMap.set(tc.call_id, tc); }

  return (
    <div className="rt-card-body">
      {/* diagnostics */}
      {hasDiag && (
        <div className="rt-diag">
          {result.errors?.map((e, i) => <div key={`e-${i}-${e.slice(0, 32)}`} className="rt-diag-e">{e}</div>)}
          {result.warnings?.map((w, i) => <div key={`w-${i}-${w.slice(0, 32)}`} className="rt-diag-w">{w}</div>)}
        </div>
      )}

      {/* steps */}
      <div className="rt-timeline">
        {events.map((evt) => {
          const t = (evt.event_type || evt.type || "").toLowerCase();
          const isTool = t.startsWith("tool_call") || evt.tool_id;
          if (isTool) {
            const match = toolMap.get(evt.tool_id || "") || toolMap.get(evt.event_id || "");
            if (match) return <ToolChip key={`tc-${match.call_id || match.tool_id}`} tc={match} />;
          }
          return <StepRow key={`ev-${evt.event_id || evt.tool_id || evt.type || evt.name}`} evt={evt} />;
        })}
        {/* tools without matching events */}
        {tools.filter((tc) => !events.some((e) => e.event_id === tc.call_id || e.tool_id === tc.call_id)).map((tc) => (
          <ToolChip key={`tc-orphan-${tc.call_id}`} tc={tc} />
        ))}
      </div>

      {retryAttempts > 0 || retryEvents.length > 0 ? (
        <div className="rt-retry-summary">
          <span className="rt-art-label">自动重试 · {retryAttempts}</span>
          <div className="rt-retry-items">
            {retryEvents.slice(0, 5).map((ev, i) => (
              <span
                key={`${ev.node_id || ev.tool_id || "retry"}-${i}`}
                className={`rt-retry-chip ${ev.retry_allowed ? "ok" : "muted"}`}
              >
                {toolLabel(String(ev.tool_id || ev.node_id || "工具"))}
                {ev.retry_allowed ? " 已尝试" : " 未重试"}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {validationCorrectionAttempts > 0 ? (
        <div className="rt-retry-summary">
          <span className="rt-art-label">参数自纠 · {validationCorrectionAttempts}</span>
          <span className={`rt-retry-chip ${validationCorrectionSummary.exhausted ? "muted" : "ok"}`}>
            {validationCorrectionSummary.exhausted ? "达到上限，已停止" : "已修正后继续"}
          </span>
        </div>
      ) : null}

      {toolRecoveryEvents.length > 0 ? (
        <div className="rt-retry-summary">
          <span className="rt-art-label">失败恢复 · {toolRecoveryEvents.length}</span>
          <span className="rt-retry-chip ok">已改策略继续</span>
        </div>
      ) : null}

      {trackingTaskId ? (
        <div className="rt-tracking-summary">
          <span className="rt-art-label">{trackingDomain ? `${trackingDomain} 跟踪` : "任务跟踪"}</span>
          <span className={`rt-tracking-chip ${trackingSummary.done ? "ok" : "live"}`}>
            {trackingStatus || "unknown"}
          </span>
          <span className="rt-tracking-id" title={trackingTaskId}>{shortId(trackingTaskId)}</span>
          {trackingSummary.progress?.percent != null && (
            <span className="rt-tracking-chip">{trackingSummary.progress.percent}%</span>
          )}
          {trackingSummary.stall_risk && (
            <span className="rt-tracking-chip warn">可能停滞</span>
          )}
        </div>
      ) : null}

      {/* artifacts */}
      {allArtifacts.length > 0 && (
        <div className="rt-artifacts">
          <span className="rt-art-label">产物 · {allArtifacts.length}</span>
          <div className="rt-chips">
            {allArtifacts.slice(0, 8).map((a) => (
              <span key={a.artifact_id} className="rt-chip">{a.title || a.artifact_id.slice(0, 12)}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});

/* ── fallback body (no result attached — only shown on load failure) ── */

const FallbackBody: React.FC<{
  userText: string;
  assistantText: string;
  loadError?: string;
  onRetry?: () => void;
}> = React.memo(
  ({ userText, assistantText, loadError, onRetry }) => (
    <div className="rt-card-body">
      <div className="rt-fallback">
        <div className="rt-fallback-row">
          <span className="rt-fallback-role">🙋 用户</span>
          <span className="rt-fallback-text">{userText || "(无内容)"}</span>
        </div>
        <div className="rt-fallback-row">
          <span className="rt-fallback-role">🤖 AI</span>
          <span className="rt-fallback-text">{assistantText || "(无内容)"}</span>
        </div>
        <div className="rt-fallback-hint">
          {loadError
            ? `加载后端执行步骤失败 · ${loadError}`
            : "详细执行步骤未加载（切换会话/刷新后由后端消息还原）"}
          {onRetry && (
            <button
              className="rt-retry-btn"
              onClick={(e) => { e.stopPropagation(); onRetry(); }}
              type="button"
            >重试</button>
          )}
        </div>
      </div>
    </div>
  ),
);

/* ── group messages into runs ── */

interface RunGroup {
  runId: string;
  userMsg?: ChatMsg;
  assistantMsg?: ChatMsg;
  result?: AgentResult;
  createdAt: string;
}

function groupMessagesIntoRuns(messages: ChatMsg[]): RunGroup[] {
  const out: RunGroup[] = [];
  const byRunId = new Map<string, RunGroup>();

  for (const m of messages) {
    if (m.role !== "user" && m.role !== "assistant") continue;
    const rid = m.run_id;
    if (rid) {
      let grp = byRunId.get(rid);
      if (!grp) {
        grp = { runId: rid, createdAt: m.created_at || "" };
        byRunId.set(rid, grp);
        out.push(grp);
      }
      if (m.role === "user") grp.userMsg = m;
      else {
        grp.assistantMsg = m;
        if (m.result) grp.result = m.result;
        if (m.created_at) grp.createdAt = m.created_at;
      }
    } else {
      // No run_id — only render assistant (user without run_id is just
      // optimistic placeholder noise, the server will fill run_id on next
      // mergeFromBackend).
      if (m.role === "assistant") {
        out.push({
          runId: `orphan-${m.id}`,
          assistantMsg: m,
          result: m.result,
          createdAt: m.created_at || "",
        });
      }
    }
  }

  // Newest first
  out.sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
  return out;
}

/* ── run card ── */

const RunCard: React.FC<{ group: RunGroup; runIdx: number }> = React.memo(({ group, runIdx }) => {
  const [open, setOpen] = useState(false);
  const result = group.result;
  const assistantText = group.assistantMsg?.text ?? "";
  const userText = group.userMsg?.text ?? "";
  const ok = result ? result.ok : group.assistantMsg?.status !== "error";
  const cardId = (result?.turn_id ?? group.runId).slice(0, 8);
  const snippet = assistantText.slice(0, 50) || userText.slice(0, 50) || "";
  const workspaceId = result?.metadata?.workspace_id;
  const hasResultBody = !!result;

  // When expanded and no result yet, fetch the full trace lazily.
  // We do NOT auto-fetch on mount — only when the user explicitly expands.
  const currentWorkspaceId = useSessionStore((s) => s.currentWorkspaceId);
  const loadRunDetail = useWorkbenchStore((s) => s.loadRunDetail);
  const loading = useWorkbenchStore((s) => !!s.runDetailLoading[group.runId]);
  const loadError = useWorkbenchStore((s) => s.runDetailError[group.runId] || "");

  React.useEffect(() => {
    if (
      open &&
      !hasResultBody &&
      !loading &&
      !loadError &&
      currentWorkspaceId &&
      group.runId &&
      !group.runId.startsWith("orphan-") // skip orphan placeholders
    ) {
      void loadRunDetail(currentWorkspaceId, group.runId);
    }
    // We intentionally depend on [open, hasResultBody] so reopening after an
    // error doesn't auto-retry (user must click the retry button).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, hasResultBody]);

  const tryLoad = () => {
    if (currentWorkspaceId && group.runId && !group.runId.startsWith("orphan-")) {
      void loadRunDetail(currentWorkspaceId, group.runId);
    }
  };

  return (
    <div className="rt-card">
      {/* ── collapsed header ── */}
      <div
        className="rt-card-bar"
        onClick={() => setOpen(!open)}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); } }}
      >
        <span className={`rt-card-dot ${ok ? "ok" : "err"}`} />
        <span className="rt-card-id">{cardId || `#${runIdx + 1}`}</span>
        {workspaceId && <span className="rt-card-ws">{workspaceId}</span>}
        <span className="rt-card-snippet">{snippet}{snippet.length > 50 ? "…" : ""}</span>
        <span className="rt-card-chev">{open ? "▲ 收起" : "▼ 展开"}</span>
      </div>

      {/* ── expanded body ── */}
      {open && (
        hasResultBody
          ? <ResultBody result={result!} />
          : loading
            ? (
              <div className="rt-card-body rt-loading">
                <span className="rt-spinner" /> 加载后端执行步骤…
              </div>
            )
            : (
              <FallbackBody
                userText={userText}
                assistantText={assistantText}
                loadError={loadError}
                onRetry={loadError ? tryLoad : undefined}
              />
            )
      )}
    </div>
  );
});

/* ── main ── */

export const RuntimeEventTimeline: React.FC<{ messages: ChatMsg[] }> = React.memo(
  function RuntimeEventTimeline({ messages }) {
    const groups = useMemo(() => groupMessagesIntoRuns(messages ?? []), [messages]);
    if (!groups || groups.length === 0) {
      return (
        <div className="rt-empty" data-testid="timeline-empty">
          <p>准备就绪</p>
          <p className="rt-empty-hint">发送消息后，执行记录将在此展示</p>
        </div>
      );
    }
    return (
      <div className="rt-list" data-testid="runtime-timeline">
        {groups.map((g, i) => <RunCard key={g.runId} group={g} runIdx={i} />)}
      </div>
    );
  },
);

export default RuntimeEventTimeline;
