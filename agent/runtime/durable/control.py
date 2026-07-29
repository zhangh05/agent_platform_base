# agent/runtime/durable/control.py
"""Phase 3: Runtime control primitives — checkpoint, cancel, retry, resume.

All operations:
- Work on TaskState as single source of truth
- Write RuntimeEvents for audit
- Enforce workspace boundary
- Redact payloads before persistence
"""

from __future__ import annotations
import time as _time
from typing import Optional
from .models import (
    TaskState, RuntimeStep, RuntimeEvent, RuntimeCheckpoint,
    StepStatus, TaskStatus, _next_id, _now,
)
from .store import save_task, append_event, save_checkpoint, get_task, get_events, get_checkpoints

# ── Idempotency / Destructive — derived from Capability Manifest ──


def _get_manifest(tool_id: str | None):
    """Look up manifest for a tool_id. Returns None if not found."""
    if not tool_id:
        return None
    try:
        from core.tools.manifest_registry import get_manifest as gm
        return gm(tool_id)
    except Exception:
        return None


def _is_retryable(step: RuntimeStep) -> bool:
    """Check if a step can be safely retried, based on manifest idempotency."""
    m = _get_manifest(step.tool_id)
    if m and m.destructive:
        return False
    if m:
        return m.idempotency == "safe_to_retry"
    # Fallback for non-tool steps: model/message/checkpoint are safe
    return step.kind in ("message", "model", "validation", "checkpoint")


# ── Checkpoint ──

def checkpoint_task(
    task_id: str, ws_id: str, reason: str = "",
    step_id: Optional[str] = None, pending_action: Optional[dict] = None,
) -> Optional[RuntimeCheckpoint]:
    """Create a checkpoint snapshot of the current TaskState."""
    task = get_task(ws_id, task_id)
    if not task:
        return None
    if task.workspace_id != ws_id:
        return None

    cp = RuntimeCheckpoint(
        checkpoint_id=_next_id("cp"),
        task_id=task_id,
        workspace_id=ws_id,
        session_id=task.session_id,
        run_id=task.run_id,
        step_id=step_id or task.current_step_id,
        state_snapshot=task.to_dict(),
        pending_action=pending_action,
        artifact_refs=list(task.artifact_ids),
        created_at=_now(),
    )
    save_checkpoint(cp)

    # Write event
    append_event(RuntimeEvent(
        event_id=_next_id("evt-cp"),
        task_id=task_id, workspace_id=ws_id,
        session_id=task.session_id, run_id=task.run_id,
        step_id=cp.step_id,
        type="checkpoint_created", status="ok",
        title=f"Checkpoint: {reason}" if reason else "Checkpoint created",
        summary=reason,
    ))
    return cp


def create_checkpoint_from_state(
    task: TaskState, reason: str = "",
    pending_action: Optional[dict] = None,
) -> Optional[RuntimeCheckpoint]:
    """Create a checkpoint directly from a TaskState instance."""
    return checkpoint_task(
        task.task_id, task.workspace_id,
        reason=reason,
        step_id=task.current_step_id,
        pending_action=pending_action,
    )


# ── Cancel ──

def cancel_task(task_id: str, ws_id: str) -> dict:
    """Cancel a running/pending task. Idempotent."""
    task = get_task(ws_id, task_id)
    if not task or task.workspace_id != ws_id:
        return {"ok": False, "error": "task not found in workspace", "status": "not_found"}

    cancellable = {"pending", "running", "interrupting", "waiting_approval"}
    if task.status not in cancellable:
        # Already terminal — idempotent return
        return {"ok": True, "status": task.status, "message": f"task already {task.status}"}

    # Mark current step cancelled
    if task.current_step_id:
        for s in task.steps:
            if s.step_id == task.current_step_id and s.status in ("pending", "running"):
                s.status = "cancelled"
                s.finished_at = _now()

    task.update_status("cancelled")
    save_task(task)

    # Checkpoint the cancelled state
    checkpoint_task(task_id, ws_id, reason="task_cancelled")

    # Write event
    append_event(RuntimeEvent(
        event_id=_next_id("evt-cancel"),
        task_id=task_id, workspace_id=ws_id,
        session_id=task.session_id, run_id=task.run_id,
        step_id=task.current_step_id,
        type="task_cancelled", status="cancelled",
        title="Task cancelled",
    ))
    return {"ok": True, "status": "cancelled", "task_id": task_id}


# ── Retry Step ──

def retry_step(task_id: str, step_id: str, ws_id: str) -> dict:
    """Retry a failed step. Only idempotent/read steps allowed."""
    task = get_task(ws_id, task_id)
    if not task or task.workspace_id != ws_id:
        return {"ok": False, "error": "task not found in workspace"}

    # Find the step
    target = None
    for s in task.steps:
        if s.step_id == step_id:
            target = s
            break
    if not target:
        return {"ok": False, "error": "step not found"}

    if target.status not in ("failed", "cancelled"):
        return {"ok": False, "error": f"step status is {target.status}, can only retry failed/cancelled"}

    # Safety: disallow destructive/non-idempotent retry (from manifest)
    if not _is_retryable(target):
        return {
            "ok": False,
            "error": f"Cannot retry non-idempotent/destructive step: {target.kind}",
            "retry_not_supported": True,
        }

    # Create retry attempt (new step referencing the original)
    retry_step_obj = RuntimeStep(
        step_id=_next_id("step-retry"),
        task_id=task_id,
        kind=target.kind,
        title=f"Retry: {target.title or step_id}",
        summary=target.summary,
        tool_id=target.tool_id,
        status="pending",
    )
    task.add_step(retry_step_obj)
    task.update_status("running")
    save_task(task)

    # Events
    append_event(RuntimeEvent(
        event_id=_next_id("evt-retry"),
        task_id=task.task_id, workspace_id=ws_id,
        session_id=task.session_id, run_id=task.run_id,
        step_id=step_id,
        type="step_retry_requested", status="ok",
        title=f"Retry step: {step_id}",
        summary=f"New attempt: {retry_step_obj.step_id}",
    ))
    return {
        "ok": True,
        "status": "retry_created",
        "original_step_id": step_id,
        "new_step_id": retry_step_obj.step_id,
    }


# ── Resume ──

def resume_task(task_id: str, ws_id: str) -> dict:
    """Resume a task from its latest checkpoint."""
    task = get_task(ws_id, task_id)
    if not task or task.workspace_id != ws_id:
        return {"ok": False, "error": "task not found in workspace"}

    resumable = {"interrupted", "waiting_approval", "failed"}
    if task.status not in resumable:
        return {"ok": False, "error": f"task status {task.status} not resumable"}

    # Find latest checkpoint
    cps = get_checkpoints(ws_id, task_id)
    if not cps:
        return {
            "ok": False,
            "error": "No checkpoint found for resume",
            "resume_not_supported": True,
        }

    latest_cp = cps[-1]
    state_snapshot = latest_cp.get("state_snapshot", {})

    # Restore from checkpoint
    task.update_status("running")
    if state_snapshot.get("current_step_id"):
        task.current_step_id = state_snapshot["current_step_id"]
    save_task(task)

    # Event
    append_event(RuntimeEvent(
        event_id=_next_id("evt-resume"),
        task_id=task.task_id, workspace_id=ws_id,
        session_id=task.session_id, run_id=task.run_id,
        step_id=latest_cp.get("step_id", ""),
        type="task_resumed", status="ok",
        title="Task resumed from checkpoint",
        summary=f"Checkpoint: {latest_cp.get('checkpoint_id', '')}",
    ))
    return {
        "ok": True,
        "status": task.status,
        "checkpoint_id": latest_cp.get("checkpoint_id"),
        "current_step_id": task.current_step_id,
    }
