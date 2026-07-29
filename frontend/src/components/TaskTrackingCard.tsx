interface Props {
  tracking: Record<string, any>;
}

export function TaskTrackingCard({ tracking }: Props) {
  const taskId = String(tracking.task_id || tracking.summary?.task_id || "");
  const domain = String(tracking.domain || tracking.raw?.domain || "");
  const status = String(tracking.status || tracking.summary?.status || "unknown");
  const done = Boolean(tracking.done || tracking.terminal);
  const progress = tracking.progress || {};
  const summary = tracking.summary || {};
  return (
    <div className="task-tracking-card">
      <div className="task-tracking-head">
        <span className={`task-tracking-status ${done ? "done" : "live"}`}>{status}</span>
        <strong>{domain ? `${domain} 任务` : "后台任务"}</strong>
        {taskId && <span className="task-tracking-id">{taskId}</span>}
      </div>
      <div className="task-tracking-body">
        {progress.percent != null && <span>进度 {progress.percent}%</span>}
        {summary.total != null && <span>总数 {summary.total}</span>}
        {summary.succeeded != null && <span>成功 {summary.succeeded}</span>}
        {summary.failed != null && <span>失败 {summary.failed}</span>}
        {tracking.next_poll_seconds ? <span>建议 {tracking.next_poll_seconds}s 后继续跟踪</span> : null}
        {tracking.stall_risk ? <span className="warn">可能停滞</span> : null}
      </div>
    </div>
  );
}
