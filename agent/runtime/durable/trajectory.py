# agent/runtime/durable/trajectory.py
"""Phase 10: Trajectory builder, eval rules, and persistence."""

from __future__ import annotations
import json, logging, re, uuid, time as _time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from storage.ids import validate_workspace_id
from storage.trajectory_store import list_trajectories as read_trajectories, read_trajectory, save_trajectory
from agent.runtime.utils import now_iso, duration_ms

def _now(): return now_iso()
def _tid(): return f"traj-{uuid.uuid4().hex[:12]}"

_log = logging.getLogger(__name__)
_TRAJECTORY_ID_RE = re.compile(r"^traj-[0-9a-f]{12}$")

_REDACT_KEYS = {"password","token","api_key","secret","credential","key","auth"}

@dataclass
class TrajectoryMetrics:
    task_success: bool = False
    tool_call_count: int = 0
    tool_failure_count: int = 0
    retry_count: int = 0
    approval_count: int = 0
    approval_reject_count: int = 0
    approval_edit_count: int = 0
    checkpoint_count: int = 0
    subagent_count: int = 0
    artifact_count: int = 0
    unverified_completion: bool = False
    memory_conflict_count: int = 0
    workspace_boundary_violation_count: int = 0
    duration_ms: int = 0
    warnings_count: int = 0
    errors_count: int = 0

@dataclass
class TrajectoryRecord:
    trajectory_id: str = field(default_factory=_tid)
    task_id: str = ""
    workspace_id: str = ""
    session_id: str = ""
    run_id: str = ""
    job_id: str = ""
    user_goal: str = ""
    final_status: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    model_provider: str = ""
    model_name: str = ""
    plan_steps: list = field(default_factory=list)
    runtime_events: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    approvals: list = field(default_factory=list)
    checkpoints: list = field(default_factory=list)
    retries: list = field(default_factory=list)
    cancellations: list = field(default_factory=list)
    subagents: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    memory_candidates: list = field(default_factory=list)
    final_answer: str = ""
    validation_result: str = ""
    user_feedback: dict = field(default_factory=dict)
    # Per-record trajectory warnings captured during build/evaluation.
    warnings: list[str] = field(default_factory=list)
    metrics: TrajectoryMetrics = field(default_factory=TrajectoryMetrics)
    redacted: bool = True
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at: self.created_at = _now()

    def to_dict(self) -> dict:
        """Convert to dict for serialization."""
        from dataclasses import asdict as _asdict
        return _asdict(self)


# ── Builder ──

def build_trajectory(task_id: str, ws_id: str) -> Optional[TrajectoryRecord]:
    """Build a full trajectory record from all runtime data sources."""
    from agent.runtime.durable.store import get_task, get_events, get_checkpoints
    task = get_task(ws_id, task_id)
    if not task: return None
    if task.workspace_id != ws_id: return None

    events = get_events(ws_id, task_id) or []
    cps = get_checkpoints(ws_id, task_id) or []

    traj = TrajectoryRecord(
        task_id=task_id, workspace_id=ws_id,
        session_id=task.session_id, run_id=task.run_id,
        job_id=task.job_id, user_goal=task.user_goal,
        final_status=task.status, started_at=task.created_at,
        plan_steps=[_redact_dict({"step_id": s.step_id if hasattr(s,'step_id') else s.get('step_id',''),
                                   "kind": s.kind if hasattr(s,'kind') else s.get('kind',''),
                                   "title": s.title if hasattr(s,'title') else s.get('title',''),
                                   "tool_id": s.tool_id if hasattr(s,'tool_id') else s.get('tool_id',''),
                                   "status": s.status if hasattr(s,'status') else s.get('status','')})
                    for s in (task.steps or [])],
    )
    traj.finished_at = task.updated_at
    if task.created_at and task.updated_at:
        try:
            traj.duration_ms = duration_ms(task.created_at, task.updated_at)
        except (TypeError, ValueError) as e:
            traj.warnings.append(f"duration calc failed: {str(e)[:100]}")

    # ── Compute metrics ──
    m = traj.metrics
    m.task_success = task.status == "succeeded"
    m.tool_call_count = len(task.tool_results or [])
    m.checkpoint_count = len(cps)
    m.artifact_count = len(task.artifact_ids or [])
    m.warnings_count = len(task.warnings or [])
    m.errors_count = len(task.errors or [])
    m.duration_ms = traj.duration_ms

    # Tool failures
    for e in events:
        t = e.get("type","")
        if t == "tool_call_failed": m.tool_failure_count += 1
        if "retry" in t: m.retry_count += 1
        if "approval" in t:
            m.approval_count += 1
            if "reject" in t: m.approval_reject_count += 1
            if "edit" in t: m.approval_edit_count += 1
        if "subagent" in t: m.subagent_count += 1
        if "cancelled" in t: traj.cancellations.append(e)
        if "conflict" in t: m.memory_conflict_count += 1
        if "violation" in t: m.workspace_boundary_violation_count += 1

    traj.runtime_events = events
    traj.final_answer = (task.warnings or task.errors or ["No content"])[0] if task.status != "succeeded" else ""

    # Unverified completion check
    if m.task_success and not any(e.get("type","") == "validation_completed" for e in events):
        m.unverified_completion = True

    return traj


def persist_trajectory(rec: TrajectoryRecord):
    ws_id = validate_workspace_id(rec.workspace_id)
    if not _TRAJECTORY_ID_RE.fullmatch(rec.trajectory_id):
        raise ValueError("invalid trajectory_id")
    save_trajectory(ws_id, rec.trajectory_id, _redact_dict(asdict(rec)))


def get_trajectory(traj_id: str, ws_id: str) -> Optional[dict]:
    ws_id = validate_workspace_id(ws_id)
    if not _TRAJECTORY_ID_RE.fullmatch(str(traj_id or "")):
        return None
    try: return read_trajectory(ws_id, traj_id)
    except Exception: return None


def list_trajectories(ws_id: str, limit=50) -> list[dict]:
    ws_id = validate_workspace_id(ws_id)
    limit = max(1, min(int(limit), 200))
    return read_trajectories(ws_id, limit)


# ── Eval rules ──

def evaluate_trajectory(traj: dict) -> dict:
    # v3.10: reject empty/undefined trajectory
    if not traj or not traj.get("task_id"):
        return {"ok": False, "severity": "critical",
                "issues": [{"rule": "invalid_trajectory", "detail": "Empty or invalid trajectory — cannot evaluate"}],
                "score": 0}
    m = traj.get("metrics", {})
    issues = []
    if m.get("task_success") is False: issues.append({"rule": "task_failed", "detail": "Task did not succeed"})
    if m.get("unverified_completion"): issues.append({"rule": "unverified_completion", "detail": "Task marked succeeded but no verification"})
    if m.get("tool_failure_count", 0) > 0: issues.append({"rule": "tool_failures", "detail": f"{m['tool_failure_count']} tool(s) failed"})
    if m.get("retry_count", 0) > 2: issues.append({"rule": "retry_loop", "detail": f"{m['retry_count']} retries"})
    if m.get("approval_reject_count", 0) > 0: issues.append({"rule": "approval_friction", "detail": f"{m['approval_reject_count']} rejections"})
    if m.get("memory_conflict_count", 0) > 0: issues.append({"rule": "memory_conflict", "detail": f"{m['memory_conflict_count']} conflicts"})
    if m.get("workspace_boundary_violation_count", 0) > 0: issues.append({"rule": "boundary_violation", "detail": f"{m['workspace_boundary_violation_count']} violations"})
    if m.get("duration_ms", 0) > 300_000: issues.append({"rule": "long_running", "detail": f"Duration {m['duration_ms']}ms > 300s"})
    if m.get("subagent_count", 0) > 0 and m.get("task_success") and not traj.get("subagents"):
        issues.append({"rule": "subagent_no_result", "detail": "Subagent spawned but no result in trajectory"})
    sev = "ok" if len(issues) == 0 else ("warning" if len(issues) <= 2 else "critical")
    return {"ok": len(issues) == 0, "severity": sev, "issues": issues, "score": max(0, 10 - len(issues) * 2)}


# ── Feedback ──

def save_feedback(traj_id: str, ws_id: str, feedback: dict) -> dict:
    ws_id = validate_workspace_id(ws_id)
    traj = get_trajectory(traj_id, ws_id)
    if not traj: return {"ok": False, "error": "trajectory not found"}
    traj["user_feedback"] = _redact_dict(feedback)
    save_trajectory(ws_id, traj_id, _redact_dict(traj))
    # Preserve user feedback as a reviewable episodic memory.
    try:
        from storage.memory_governance import MemoryRecord, MemoryWriteGate
        gate = MemoryWriteGate()
        rec = MemoryRecord(
            workspace_id=ws_id, session_id=traj.get("session_id",""),
            task_id=traj.get("task_id",""), scope="task",
            memory_type="episodic_case", status="pending",
            source="user", confidence=0.9,
            content=f"Feedback: {feedback.get('comment','')[:200]} Rating: {feedback.get('rating','')}",
            summary=feedback.get("comment","")[:100],
            citations=[{"trajectory_id": traj_id}],
        )
        gate.write(rec)
    except Exception:
        # Feedback memory write is best-effort, not critical
        _log.warning("trajectory feedback memory candidate write failed", exc_info=True)
    return {"ok": True}


# ── Helpers ──

# Module-level live task registry for cancel/status operations.
# Populated by run_subagent_task, read by handle_agent_cancel/status.
_live_tasks: dict[str, dict] = {}


def _redact_dict(d: dict) -> dict:
    if not isinstance(d, dict): return d
    out = {}
    for k, v in d.items():
        if any(rk in k.lower() for rk in _REDACT_KEYS):
            out[k] = "[REDACTED]"
        elif isinstance(v, dict):
            out[k] = _redact_dict(v)
        elif isinstance(v, str) and len(v) > 500:
            out[k] = v[:500] + "..."
        else:
            out[k] = v
    return out
