"""Job lifecycle helpers — unified entry point for attaching runs to session jobs.

Both HTTP (agent_routes.py) and WebSocket (agent_ws.py) paths call into
this module to avoid code duplication and ensure consistent behavior.

Responsibilities:
  1. Find or create the agent_run job for a session
  2. Reactivate succeeded/cancelled jobs via the state machine
  3. Append run_id and update progress
"""

import logging
from typing import Any

from jobs.store import get_job, update_job, list_jobs
from jobs.manager import create_job, mark_failed, mark_running, mark_succeeded, update_progress
from storage.time_utils import now_iso

_log = logging.getLogger("jobs.lifecycle")


def _broadcast_job(job_id: str, ws_id: str, session_id: str = "") -> None:
    """Push job_updated event with full artifact info to all WebSocket clients."""
    try:
        from jobs.store import get_job
        rec = get_job(ws_id, job_id)
        if not rec:
            return
        data = {
            "job_id": job_id, "workspace_id": ws_id, "session_id": session_id,
            "status": rec.status, "title": rec.title,
            "run_ids": getattr(rec, "run_ids", []) or [],
            "output_artifacts": getattr(rec, "output_artifacts", []) or [],
            "progress": dict(getattr(rec, "progress", {}) or {}),
            "active_turn": dict((getattr(rec, "metadata", {}) or {}).get("active_turn") or {}),
        }
        from backend.ws.agent_ws import broadcast_ws_event
        broadcast_ws_event({"name": "job_updated", "data": data})
    except Exception:
        pass


_STAGE_PROGRESS: dict[str, tuple[int, str]] = {
    "turn_started": (1, "理解问题"),
    "planner_started": (1, "理解问题"),
    "planner_completed": (1, "理解问题"),
    "graph_compiled": (1, "理解问题"),
    "structural_validated": (1, "理解问题"),
    "semantic_validated": (1, "理解问题"),
    "semantic_invalid": (1, "理解问题"),
    "pre_repair_started": (1, "理解问题"),
    "pre_repair_completed": (1, "理解问题"),
    "risk_assessed": (1, "理解问题"),
    "budget_ok": (1, "理解问题"),
    "execution_started": (2, "收集证据"),
    "orchestration_planned": (2, "收集证据"),
    "orchestration_layer_started": (2, "收集证据"),
    "orchestration_layer_completed": (2, "收集证据"),
    "tool_call": (2, "收集证据"),
    "tool_result": (2, "收集证据"),
    "execution_completed": (2, "收集证据"),
    "repair_attempt": (2, "收集证据"),
    "merge_completed": (3, "分析判断"),
    "response_started": (3, "分析判断"),
    "model_started": (3, "分析判断"),
    "response_completed": (4, "形成建议"),
    "turn_completed": (4, "形成建议"),
}


def begin_session_turn(
    ws_id: str,
    session_id: str,
    user_input: str,
    *,
    client_request_id: str = "",
) -> str | None:
    """Create a durable, user-visible snapshot before runtime work begins.

    The job is session-scoped, while ``active_turn`` represents exactly one
    request.  Updating this snapshot lets a refreshed browser recover the
    current stage without replaying an operation or inventing progress.
    """
    if not session_id:
        return None
    job_id = _find_or_create_job(ws_id, session_id, user_input)
    if not job_id:
        return None
    _ensure_running(ws_id, job_id)
    rec = get_job(ws_id, job_id)
    if not rec:
        return None
    metadata = dict(rec.metadata or {})
    metadata["active_turn"] = {
        "client_request_id": str(client_request_id or ""),
        "session_id": session_id,
        "status": "running",
        "stage": "turn_started",
        "stage_label": "理解问题",
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "events": [],
        "tool_calls": [],
        "run_id": "",
        "trace_id": "",
        "error": "",
    }
    update_job(ws_id, job_id, {
        "metadata": metadata,
        "progress": {
            "current": 1,
            "total": 4,
            "percent": 25,
            "message": "正在理解问题",
            "current_step": "理解问题",
            "updated_at": now_iso(),
        },
    })
    _broadcast_job(job_id, ws_id, session_id)
    return job_id


def update_session_turn_stage(
    ws_id: str,
    job_id: str,
    session_id: str,
    event: dict[str, Any],
) -> None:
    """Persist one compact runtime event and broadcast the projected phase."""
    if not job_id or not isinstance(event, dict):
        return
    rec = get_job(ws_id, job_id)
    if not rec:
        return
    metadata = dict(rec.metadata or {})
    active = dict(metadata.get("active_turn") or {})
    if active.get("status") != "running":
        return
    stage = str(event.get("type") or event.get("name") or "event")
    current, label = _STAGE_PROGRESS.get(stage, (
        int((rec.progress or {}).get("current") or 1),
        str((rec.progress or {}).get("current_step") or "理解问题"),
    ))
    compact = {
        "type": stage,
        "timestamp": event.get("timestamp"),
        "elapsed_ms": int(event.get("elapsed_ms") or 0),
    }
    tool_id = str(event.get("tool_id") or event.get("name") or "") if stage in {"tool_call", "tool_result"} else ""
    if tool_id:
        compact["tool_id"] = tool_id
        compact["call_id"] = str(event.get("call_id") or "")
        if stage == "tool_result":
            compact["ok"] = bool(event.get("ok", event.get("status") == "ok"))
            compact["summary"] = str(event.get("summary") or event.get("message") or "")[:240]

    events = [item for item in list(active.get("events") or []) if isinstance(item, dict)]
    events.append(compact)
    active["events"] = events[-80:]

    tools = [item for item in list(active.get("tool_calls") or []) if isinstance(item, dict)]
    if tool_id:
        call_id = str(event.get("call_id") or "")
        match_index = next((
            index for index, item in enumerate(tools)
            if (call_id and item.get("call_id") == call_id)
            or (not call_id and item.get("tool_id") == tool_id and item.get("status") == "running")
        ), -1)
        next_tool = {
            "call_id": call_id,
            "tool_id": tool_id,
            "status": "done" if stage == "tool_result" and compact.get("ok") else (
                "failed" if stage == "tool_result" else "running"
            ),
            "ok": compact.get("ok", False),
            "summary": compact.get("summary", ""),
        }
        if match_index >= 0:
            tools[match_index] = {**tools[match_index], **next_tool}
        else:
            tools.append(next_tool)
        active["tool_calls"] = tools[-24:]

    active.update({
        "stage": stage,
        "stage_label": label,
        "updated_at": now_iso(),
    })
    metadata["active_turn"] = active
    update_job(ws_id, job_id, {
        "metadata": metadata,
        "progress": {
            "current": current,
            "total": 4,
            "percent": current * 25,
            "message": f"正在{label}",
            "current_step": label,
            "updated_at": now_iso(),
        },
    })
    _broadcast_job(job_id, ws_id, session_id)


def finish_session_turn_snapshot(
    ws_id: str,
    job_id: str,
    session_id: str,
    *,
    run_id: str = "",
    trace_id: str = "",
    ok: bool,
    error: str = "",
) -> None:
    """Close the durable turn snapshot before the session job is finalized."""
    if not job_id:
        return
    rec = get_job(ws_id, job_id)
    if not rec:
        return
    metadata = dict(rec.metadata or {})
    active = dict(metadata.get("active_turn") or {})
    active.update({
        "status": "succeeded" if ok else "failed",
        "stage": "turn_completed" if ok else "turn_failed",
        "stage_label": "形成建议" if ok else "处理失败",
        "updated_at": now_iso(),
        "finished_at": now_iso(),
        "run_id": str(run_id or ""),
        "trace_id": str(trace_id or ""),
        "error": str(error or "")[:240],
    })
    metadata["active_turn"] = active
    update_job(ws_id, job_id, {
        "metadata": metadata,
        "progress": {
            "current": 4,
            "total": 4,
            "percent": 100,
            "message": "处理完成" if ok else "处理失败",
            "current_step": "形成建议" if ok else "处理失败",
            "updated_at": now_iso(),
        },
    })
    _broadcast_job(job_id, ws_id, session_id)


def attach_run_to_session_job(
    ws_id: str,
    session_id: str,
    run_id: str,
    tool_call_count: int = 0,
    user_input: str = "",
    run_ok: bool = True,
    error: str = "",
) -> str | None:
    """Find or create the session's agent_run job and attach a run_id.

    Returns job_id on success, None on failure.
    """
    if not session_id:
        return None

    job_id = _find_or_create_job(ws_id, session_id, user_input)
    if not job_id:
        return None

    _ensure_running(ws_id, job_id)
    _merge_run_id(ws_id, job_id, session_id, run_id, tool_call_count)
    _finish_turn(ws_id, job_id, session_id, run_id, run_ok=run_ok, error=error)
    return job_id


def _finish_turn(
    ws_id: str,
    job_id: str,
    session_id: str,
    run_id: str,
    *,
    run_ok: bool,
    error: str = "",
) -> None:
    """Close the current turn while retaining one reusable job per session.

    Leaving session jobs in ``running`` between messages made every planned
    backend restart look like an interrupted task.  Terminal turns are marked
    succeeded/failed here; the next user message reactivates the same job.
    """
    try:
        if run_ok:
            mark_succeeded(ws_id, job_id, result_summary={"latest_run_id": run_id})
        else:
            mark_failed(
                ws_id,
                job_id,
                error=error or "agent_turn_failed",
                result_summary={"latest_run_id": run_id},
            )
        _broadcast_job(job_id, ws_id, session_id)
    except (TypeError, ValueError):
        _log.exception("job turn finalization failed job=%s run=%s", job_id, run_id)


def _find_or_create_job(ws_id: str, session_id: str, user_input: str) -> str | None:
    """Find existing agent_run job for session, or create a new one."""
    job_id = None
    for j in list_jobs(ws_id=ws_id, limit=500):
        p = j.get("payload", {}) or {}
        if p.get("session_id") == session_id:
            job_id = j.get("job_id", "")
            break

    if not job_id:
        title = user_input[:40].replace("\n", " ") if user_input else "agent_run"
        try:
            from storage.session_store import get_session
            s = get_session(session_id, ws_id)
            if s and s.get("title"):
                title = s["title"]
        except Exception:
            _log.warning("session title lookup failed session=%s ws=%s", session_id, ws_id)

        j = create_job(
            workspace_id=ws_id, job_type="agent_run", title=title,
            payload={"session_id": session_id}, created_by="api",
        )
        job_id = j.get("job_id") if isinstance(j, dict) else j.job_id
        _log.info("job created: %s for session=%s title=%.40s", job_id, session_id, title)
        _broadcast_job(job_id, ws_id, session_id)

    return job_id


def _ensure_running(ws_id: str, job_id: str):
    """Ensure job is in running state, reactivating from succeeded/cancelled if needed.

    The state machine allows succeeded→running and cancelled→running.
    """
    rec = get_job(ws_id, job_id)
    if not rec:
        return

    if rec.status in ("created", "queued", "failed", "succeeded", "cancelled"):
        try:
            mark_running(ws_id, job_id)
            _broadcast_job(job_id, ws_id)
            _log.debug("job marked running: %s (was %s)", job_id, rec.status)
        except ValueError as e:
            _log.warning("mark_running failed for job=%s status=%s: %s", job_id, rec.status, e)


def _merge_run_id(ws_id: str, job_id: str, session_id: str, run_id: str, tool_call_count: int):
    """Append run_id to job, merging session run_ids for orphan recovery."""
    if not run_id:
        return

    rec = get_job(ws_id, job_id)
    if not rec:
        return

    new_ids = list(getattr(rec, "run_ids", None) or [])

    # Recovery: merge session run_ids that might be missing from job
    try:
        from storage.session_store import get_session
        s = get_session(session_id, ws_id)
        if s:
            for rid in (s.get("run_ids") or []):
                if rid and rid not in new_ids:
                    new_ids.append(rid)
    except Exception:
        _log.warning("run_ids merge failed session=%s ws=%s", session_id, ws_id)

    if run_id not in new_ids:
        new_ids.append(run_id)

    # ── Merge artifact refs from run records ───────────────────────────
    # Agent turns (via run_store) carry artifact_refs in their context.
    # Inspection tasks and other tools write artifacts via save_artifact
    # with a run_id that may differ from the agent turn_id.  We pull
    # artifact_refs from the run record so the job "Artifacts" tab is
    # populated even when the artifact store run index uses a different id.
    output_arts = list(getattr(rec, "output_artifacts", None) or [])
    trace_ids = list(getattr(rec, "trace_ids", None) or [])
    run_started_at = ""
    try:
        from storage.run_record_store import get_run
        for rid in new_ids:
            run_rec = get_run(rid, ws_id)
            if run_rec:
                if rid == run_id:
                    run_started_at = str(run_rec.get("started_at") or run_rec.get("created_at") or "")
                    trace_id = str(run_rec.get("trace_id") or "")
                    if trace_id and trace_id not in trace_ids:
                        trace_ids.append(trace_id)
                for ref in (run_rec.get("artifact_refs") or []):
                    art_id = ref.get("artifact_id") if isinstance(ref, dict) else ref
                    if art_id and art_id not in output_arts:
                        output_arts.append(art_id)
    except Exception:
        _log.debug("artifact_refs merge failed job=%s", job_id, exc_info=True)

    patch = {
        "run_ids": new_ids,
        "trace_ids": trace_ids,
        "output_artifacts": output_arts,
    }
    if run_started_at:
        patch["started_at"] = run_started_at
    update_job(ws_id, job_id, patch)
    update_progress(
        ws_id, job_id,
        current=len(new_ids),
        message=f"{len(new_ids)}轮 | {tool_call_count}工具调用",
    )
    _log.info("job updated: %s runs=%d tools=%d artifacts=%d", job_id, len(new_ids), tool_call_count, len(output_arts))
    _broadcast_job(job_id, ws_id, session_id)
