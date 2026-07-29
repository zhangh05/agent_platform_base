"""Job lifecycle helpers — unified entry point for attaching runs to session jobs.

Both HTTP (agent_routes.py) and WebSocket (agent_ws.py) paths call into
this module to avoid code duplication and ensure consistent behavior.

Responsibilities:
  1. Find or create the agent_run job for a session
  2. Reactivate succeeded/cancelled jobs via the state machine
  3. Append run_id and update progress
"""

import logging

from jobs.store import get_job, update_job, list_jobs
from jobs.manager import create_job, mark_running, update_progress

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
            "progress": (rec.progress or {}).get("current", 0) if hasattr(rec, "progress") else 0,
        }
        from backend.ws.agent_ws import broadcast_ws_event
        broadcast_ws_event({"name": "job_updated", "data": data})
    except Exception:
        pass


def attach_run_to_session_job(
    ws_id: str,
    session_id: str,
    run_id: str,
    tool_call_count: int = 0,
    user_input: str = "",
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
    return job_id


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

    if rec.status in ("created", "queued", "succeeded", "cancelled"):
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
    try:
        from storage.run_record_store import get_run
        for rid in new_ids:
            run_rec = get_run(rid, ws_id)
            if run_rec:
                for ref in (run_rec.get("artifact_refs") or []):
                    art_id = ref.get("artifact_id") if isinstance(ref, dict) else ref
                    if art_id and art_id not in output_arts:
                        output_arts.append(art_id)
    except Exception:
        _log.debug("artifact_refs merge failed job=%s", job_id, exc_info=True)

    update_job(ws_id, job_id, {"run_ids": new_ids, "output_artifacts": output_arts})
    update_progress(
        ws_id, job_id,
        current=len(new_ids),
        message=f"{len(new_ids)}轮 | {tool_call_count}工具调用",
    )
    _log.info("job updated: %s runs=%d tools=%d artifacts=%d", job_id, len(new_ids), tool_call_count, len(output_arts))
    _broadcast_job(job_id, ws_id, session_id)
