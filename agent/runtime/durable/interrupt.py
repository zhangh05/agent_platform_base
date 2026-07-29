# agent/runtime/durable/interrupt.py
"""v3.10: Durable interrupt/resume primitives for approval flow.

All approval is task_id/step_id-bound. No guessing by session or tool_id.
"""

from __future__ import annotations
from typing import Optional, Literal
import uuid, time as _time, json
from agent.runtime.utils import now_iso

Decision = Literal["approve", "reject", "edit_args", "respond", "respond_with_feedback"]

def _now(): return now_iso()
def _next_id(prefix: str) -> str: return f"{prefix}-{uuid.uuid4().hex[:8]}"

_SECRET_PATTERNS = {"password", "token", "api_key", "secret", "credential",
                     "private_key", "access_key", "auth", "authorization",
                     "x-api-token", "x-admin-token", "bearer"}


def _redact_args(args: dict) -> dict:
    if not isinstance(args, dict):
        return {}
    out = {}
    for k, v in args.items():
        kl = k.lower()
        if any(s in kl for s in _SECRET_PATTERNS):
            out[k] = "[REDACTED]"
        elif isinstance(v, str) and len(v) > 256:
            out[k] = v[:256] + "..."
        elif isinstance(v, (int, float, bool, type(None))):
            out[k] = v
        elif isinstance(v, list):
            out[k] = f"[{len(v)} items]"
        elif isinstance(v, dict):
            out[k] = _redact_args(v)
        else:
            out[k] = str(v)[:200]
    return out


def _hash_args(args: dict) -> str:
    try:
        return f"sha256:{hash(json.dumps(args, sort_keys=True, default=str))}"
    except Exception:
        return "hash_error"


def interrupt_before_tool(
    task_id: str,
    ws_id: str,
    session_id: str,
    run_id: str,
    step_id: str,
    tool_invocation: dict,
    risk_decision: dict,
) -> dict:
    """v3.10: Interrupt before tool — creates checkpoint, sets waiting_approval.

    ALL parameters are explicit (no guessing by session or tool_id).
    Returns dict with approval_id, pending_action_id, checkpoint_id.
    """
    # 1. Build pending_action (redacted)
    pending_action = {
        "action_id": _next_id("act"),
        "type": "tool_call",
        "tool_id": tool_invocation.get("tool_id", ""),
        "step_id": step_id,
        "input_args_redacted": _redact_args(tool_invocation.get("arguments", {})),
        "input_args_hash": _hash_args(tool_invocation.get("arguments", {})),
        "risk_level": risk_decision.get("risk_level", "high"),
        "created_at": _now(),
    }

    # 2. Get task state from durable store
    from .store import get_task, save_task
    task = get_task(ws_id, task_id)
    if not task:
        return {"ok": False, "error": "task not found"}
    if task.workspace_id != ws_id:
        return {"ok": False, "error": "workspace mismatch"}

    # 3. Create RuntimeCheckpoint with pending_action
    from .models import RuntimeCheckpoint
    cp = RuntimeCheckpoint(
        checkpoint_id=_next_id("cp-int"),
        task_id=task_id, workspace_id=ws_id,
        session_id=session_id, run_id=run_id,
        step_id=step_id,
        state_snapshot=task.to_dict() if hasattr(task, 'to_dict') else {},
        pending_action=pending_action,
        artifact_refs=list(getattr(task, 'artifact_ids', []) or []),
    )
    from .store import save_checkpoint
    save_checkpoint(cp)

    # 4. Create ApprovalStore request — get approval_id from return
    approval_id = ""
    try:
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id=session_id,
            tool_id=tool_invocation.get("tool_id", ""),
            arguments=tool_invocation.get("arguments", {}),
            description=risk_decision.get("reason", "High-risk tool requires approval"),
            risk_level=risk_decision.get("risk_level", "high"),
            workspace_id=ws_id,
            run_id=run_id,
            job_id=getattr(task, 'job_id', '') or '',
            metadata={
                "task_id": task_id,
                "step_id": step_id,
                "pending_action_id": pending_action["action_id"],
            },
        )
        approval_id = req.approval_id
    except Exception as e:
        task.warnings.append(f"ApprovalStore create failed: {str(e)[:100]}")

    if not approval_id:
        return {"ok": False, "error": "failed to create approval"}

    # 5. Update TaskState → waiting_approval
    task.update_status("waiting_approval")
    task.pending_approval_id = approval_id
    task.pending_action_id = pending_action["action_id"]
    task.current_step_id = step_id
    save_task(task)

    # 6. Emit events
    from .models import RuntimeEvent
    from .store import append_event
    append_event(RuntimeEvent(
        event_id=_next_id("evt-approval"),
        task_id=task_id, workspace_id=ws_id,
        session_id=session_id, run_id=run_id, step_id=step_id,
        type="approval_required", status="pending",
        title="Approval required",
        summary=f"Tool {tool_invocation.get('tool_id','')}: {risk_decision.get('reason','')}"[:200],
        payload_redacted={"risk_level": risk_decision.get("risk_level", ""),
                          "approval_id": approval_id},
    ))
    append_event(RuntimeEvent(
        event_id=_next_id("evt-interrupt"),
        task_id=task_id, workspace_id=ws_id,
        session_id=session_id, run_id=run_id, step_id=step_id,
        type="task_interrupted", status="interrupting",
        title="Task interrupted for approval",
    ))

    return {
        "ok": True,
        "status": "waiting_approval",
        "approval_id": approval_id,
        "pending_action_id": pending_action["action_id"],
        "checkpoint_id": cp.checkpoint_id,
        "task_id": task_id,
        "step_id": step_id,
    }


def resume_after_approval(
    task_id: str, ws_id: str, approval_id: str,
    decision: Decision,
    edited_args: Optional[dict] = None,
    feedback: str = "",
    reason: str = "",
) -> dict:
    """Resume a task after approval decision.

    decision: approve | reject | edit_args | respond_with_feedback
    Returns dict with ok, status, decision, and execution result if approved.
    """
    from .store import get_task, save_task, append_event
    from .models import RuntimeEvent
    task = get_task(ws_id, task_id)
    if not task or task.workspace_id != ws_id:
        return {"ok": False, "error": "task not found in workspace"}

    if task.status != "waiting_approval":
        return {"ok": False, "error": f"task status is {task.status}, not waiting_approval"}

    if task.pending_approval_id and task.pending_approval_id != approval_id:
        return {"ok": False, "error": "approval_id mismatch — bound to different pending action"}

    sid = getattr(task, 'session_id', '')
    rid = getattr(task, 'run_id', '')
    step_id = getattr(task, 'current_step_id', '')

    if decision == "approve":
        task.update_status("running")
        task.pending_approval_id = None
        task.pending_action_id = ""
        for s in task.steps:
            if s.step_id == step_id and s.status == "waiting_approval":
                s.status = "running"
        save_task(task)

        append_event(RuntimeEvent(
            event_id=_next_id("evt-approved"), task_id=task_id, workspace_id=ws_id,
            session_id=sid, run_id=rid, step_id=step_id,
            type="approval_approved", status="approved",
            title=f"Approval granted for {step_id}",
            summary=reason or "User approved",
        ))
        append_event(RuntimeEvent(
            event_id=_next_id("evt-resume"), task_id=task_id, workspace_id=ws_id,
            session_id=sid, run_id=rid, step_id=step_id,
            type="task_resumed", status="ok",
            title="Task resumed after approval",
        ))
        return {"ok": True, "status": "running", "decision": "approved",
                "task_id": task_id, "step_id": step_id}

    elif decision == "reject":
        task.update_status("failed")
        task.pending_approval_id = None
        task.pending_action_id = ""
        task.errors.append(f"approval_rejected: {reason or 'User denied'}")
        for s in task.steps:
            if s.step_id == step_id:
                s.status = "failed"
                s.summary = f"Rejected: {reason or 'User denied'}"
        save_task(task)

        append_event(RuntimeEvent(
            event_id=_next_id("evt-rejected"), task_id=task_id, workspace_id=ws_id,
            session_id=sid, run_id=rid, step_id=step_id,
            type="approval_rejected", status="rejected",
            title="Approval rejected", summary=reason or "User denied",
        ))
        return {"ok": True, "status": "failed", "decision": "rejected"}

    elif decision == "edit_args":
        if not edited_args:
            return {"ok": False, "error": "edited_args required for edit_args decision"}
        if not isinstance(edited_args, dict):
            return {"ok": False, "error": "edited_args must be a dict"}

        task.update_status("running")
        task.pending_approval_id = None
        task.pending_action_id = ""
        try:
            task.tool_results.append({"__edited_args__": edited_args, "step_id": step_id})
        except Exception as e:
            task.warnings.append(f"Failed to store edited_args: {str(e)[:100]}")
        for s in task.steps:
            if s.step_id == step_id:
                s.status = "running"
                s.summary = f"Args edited: {str(edited_args)[:100]}"
        save_task(task)

        append_event(RuntimeEvent(
            event_id=_next_id("evt-edited"), task_id=task_id, workspace_id=ws_id,
            session_id=sid, run_id=rid, step_id=step_id,
            type="approval_args_edited", status="edited",
            title="Approval args edited",
            summary=f"Args edited: keys={list(edited_args.keys())}",
        ))
        append_event(RuntimeEvent(
            event_id=_next_id("evt-resume"), task_id=task_id, workspace_id=ws_id,
            session_id=sid, run_id=rid, step_id=step_id,
            type="task_resumed", status="ok",
            title="Task resumed with edited args",
        ))
        return {"ok": True, "status": "running", "decision": "edit_args",
                "edited_args_keys": list(edited_args.keys())}

    elif decision in ("respond", "respond_with_feedback"):
        task.update_status("interrupted")
        task.warnings.append(f"User feedback: {feedback or reason}")
        save_task(task)

        append_event(RuntimeEvent(
            event_id=_next_id("evt-feedback"), task_id=task_id, workspace_id=ws_id,
            session_id=sid, run_id=rid, step_id=step_id,
            type="approval_responded", status="interrupted",
            title="User responded with feedback",
            summary=(feedback or reason)[:200],
        ))
        return {"ok": True, "status": "interrupted", "decision": "respond",
                "feedback": (feedback or reason)[:500]}

    return {"ok": False, "error": f"unknown decision: {decision}"}
