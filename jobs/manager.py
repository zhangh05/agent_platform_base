# jobs/manager.py
"""Job manager — strict state machine, lifecycle operations."""

import logging

from agent.runtime.utils import now_iso
from jobs.schemas import JobRecord, JobEvent, JobProgress, ENABLED_JOB_TYPES
from jobs.store import create_job as _create, get_job, update_job, append_event, append_log
from jobs.redaction import sanitize_job_record_for_api, sanitize_job_record_for_storage

_LOG = logging.getLogger(__name__)

# Strict transition table
ALLOWED_TRANSITIONS = {
    "created": {"queued", "cancelled"},
    "queued": {"running", "cancelled", "failed"},
    "running": {"succeeded", "failed", "cancelled", "paused"},
    "paused": {"running", "cancelled", "failed"},
    # A new user turn may reactivate a failed session-level job.  This is not
    # an automatic retry of the failed turn; it is new work in the same
    # conversation, so it must not remain permanently labelled failed.
    "failed": {"queued", "running", "cancelled"},
    "succeeded": {"running"},    # allow session jobs to re-activate
    "cancelled": {"running"},    # allow cancelled jobs to be re-activated
}

# Planned jobs can transition directly (no actual work)
PLANNED_TRANSITIONS = {"created": {"running"}}


def _record_job_status(status: str) -> None:
    try:
        from observability.metrics import record_operation
        record_operation("job", status)
    except (ImportError, TypeError, ValueError):
        _LOG.debug("job metric update failed", exc_info=True)


def _check_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    allowed = ALLOWED_TRANSITIONS.get(current, set()) | PLANNED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"invalid_transition: {current} → {target}")
    return True


def create_job(workspace_id="default", job_type="agent_run", title="", payload=None,
               input_artifacts=None, created_by="user", enqueue=True) -> JobRecord:
    if job_type not in ENABLED_JOB_TYPES:
        raise ValueError(f"unsupported job_type: {job_type}")

    payload = dict(payload or {})

    rec = JobRecord(
        workspace_id=workspace_id, job_type=job_type,
        title=title or f"{job_type} job",
        payload=payload,
        input_artifacts=input_artifacts or [],
        created_by=created_by, status="created",
    )
    rec = _create(rec)
    # Every job created inside a side-effecting tool call inherits the
    # server-owned operation correlation. This closes the crash window between
    # durable job creation and the tool response without trusting model input.
    from core.tools.context import get_runtime_operation_context
    operation = get_runtime_operation_context()
    if operation and operation[0] == workspace_id:
        from core.runtime_engine.operation_ledger import link_operation_resource
        link_operation_resource(
            workspace_id,
            operation[1],
            resource_kind="job",
            resource_id=rec.job_id,
        )
    if enqueue:
        rec = enqueue_job(workspace_id, rec.job_id)
    return rec


def enqueue_job(ws_id, job_id) -> JobRecord:
    rec = get_job(ws_id, job_id)
    if not rec: raise ValueError("job not found")
    _check_transition(rec.status, "queued")
    result = _transition(ws_id, job_id, "queued", "job_queued", "Job queued")
    from jobs.queue import get_job_queue
    get_job_queue().enqueue(ws_id, job_id)
    return result


def cancel_job(ws_id, job_id, *, expected_client_request_id="") -> JobRecord:
    rec = get_job(ws_id, job_id)
    if not rec: raise ValueError("job not found")
    if rec.status == "queued":
        _check_transition(rec.status, "cancelled")
        append_event(ws_id, job_id, JobEvent(job_id=job_id, workspace_id=ws_id,
                     event_type="job_cancelled", message="Job cancelled from queue"))
        return _transition(ws_id, job_id, "cancelled", "job_cancelled")
    elif rec.status == "running":
        from jobs.store import request_job_cancellation
        result, applied = request_job_cancellation(
            ws_id,
            job_id,
            expected_client_request_id=expected_client_request_id,
        )
        if expected_client_request_id and not applied:
            current = dict((getattr(result, "metadata", {}) or {}).get("active_turn") or {})
            actual = str(current.get("client_request_id") or "")
            if getattr(result, "status", "") == "running" and actual != str(expected_client_request_id):
                raise ValueError("stale_turn")
        if not applied:
            return result or rec
        append_event(ws_id, job_id, JobEvent(job_id=job_id, workspace_id=ws_id,
                     event_type="job_cancel_requested", message="Cancel requested"))
        return result
    elif rec.status in ("failed", "cancelled"):
        return rec
    return rec


def retry_job(ws_id, job_id, force=False) -> JobRecord:
    rec = get_job(ws_id, job_id)
    if not rec: raise ValueError("job not found")
    if not force:
        _check_transition(rec.status, "queued")
    if rec.retry_count >= rec.max_retries:
        raise ValueError("retry_limit_exceeded")
    patch = {"retry_count": rec.retry_count + 1, "status": "queued", "error": "", "cancel_requested": False}
    result = update_job(ws_id, job_id, patch)
    from jobs.queue import get_job_queue
    get_job_queue().enqueue(ws_id, job_id)
    append_event(ws_id, job_id, JobEvent(job_id=job_id, workspace_id=ws_id,
                 event_type="job_retried", message=f"Retry #{rec.retry_count + 1}"))
    _record_job_status("queued")
    return result


def mark_running(ws_id, job_id) -> JobRecord:
    rec = get_job(ws_id, job_id)
    if not rec: raise ValueError("job not found")
    _check_transition(rec.status, "running")
    now = now_iso()
    result = _transition(ws_id, job_id, "running", "job_started", "Job started")
    if result:
        update_job(ws_id, job_id, {
            "started_at": now,
            "finished_at": "",
            "error": "",
            "cancel_requested": False,
        })
    return result


def mark_succeeded(ws_id, job_id, result_summary=None) -> JobRecord:
    rec = get_job(ws_id, job_id)
    if not rec: return None
    _check_transition(rec.status, "succeeded")
    now = now_iso()
    patch = {"status": "succeeded", "finished_at": now}
    if result_summary: patch["result_summary"] = result_summary
    result = update_job(ws_id, job_id, patch)
    if result:
        append_event(ws_id, job_id, JobEvent(job_id=job_id, workspace_id=ws_id,
                     event_type="job_succeeded", message="Job succeeded"))
        _record_job_status("succeeded")
    return result


def mark_failed(ws_id, job_id, error="", result_summary=None) -> JobRecord:
    rec = get_job(ws_id, job_id)
    if not rec: return None
    _check_transition(rec.status, "failed")
    now = now_iso()
    error = str(error)[:500]
    patch = {"status": "failed", "finished_at": now, "error": error}
    if result_summary:
        patch["result_summary"] = result_summary
    result = update_job(ws_id, job_id, patch)
    if result:
        append_event(ws_id, job_id, JobEvent(job_id=job_id, workspace_id=ws_id,
                     event_type="job_failed", message=f"Job failed: {error[:100]}"))
        _record_job_status("failed")
    return result


def mark_cancelled(ws_id, job_id, message="Job cancelled") -> JobRecord:
    rec = get_job(ws_id, job_id)
    if not rec:
        return None
    _check_transition(rec.status, "cancelled")
    result = update_job(ws_id, job_id, {
        "status": "cancelled",
        "finished_at": now_iso(),
        "cancel_requested": True,
    })
    if result:
        append_event(ws_id, job_id, JobEvent(
            job_id=job_id,
            workspace_id=ws_id,
            event_type="job_cancelled",
            message=message,
        ))
        _record_job_status("cancelled")
    return result


def update_progress(ws_id, job_id, current=None, total=None, message="", step=""):
    rec = get_job(ws_id, job_id)
    if not rec: return
    prog = dict(rec.progress) if rec.progress else {}
    if current is not None: prog["current"] = current
    if total is not None: prog["total"] = total
    if total: prog["percent"] = min(100, int((prog.get("current", 0) / total) * 100))
    if message: prog["message"] = message
    if step: prog["current_step"] = step
    prog["updated_at"] = now_iso()
    update_job(ws_id, job_id, {"progress": prog})
    append_event(ws_id, job_id, JobEvent(job_id=job_id, workspace_id=ws_id,
                 event_type="job_progress", message=message or f"Progress: {prog.get('current', 0)}/{prog.get('total', 0)}",
                 progress=dict(prog)))


def _transition(ws_id, job_id, target, evt_type, msg=""):
    patch = {"status": target}
    rec = update_job(ws_id, job_id, patch)
    if rec:
        append_event(ws_id, job_id, JobEvent(job_id=job_id, workspace_id=ws_id,
                     event_type=evt_type, message=msg))
        _record_job_status(target)
    return rec
