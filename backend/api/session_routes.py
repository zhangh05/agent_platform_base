"""Session API routes — conversation management endpoints.

All endpoints follow the pattern:
  /api/sessions          — global list / create
  /api/sessions/<id>     — get / update / delete a session
  /api/sessions/<id>/... — archive, restore, messages
"""

import logging

from flask import request, jsonify

from storage.ids import validate_workspace_id, validate_session_id
from storage.session_store import (
    create_session,
    get_session,
    list_sessions,
    update_session,
    archive_session,
    soft_delete_session,
    delete_session_permanently,
    get_session_messages,
    get_or_create_default_session,
    auto_title_from_input,
    get_session_count,
)

_log = logging.getLogger(__name__)


def _invalid_ws():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _invalid_sid():
    return jsonify({"ok": False, "error": "invalid_session_id"}), 400


def _validated_ws_id(raw="default"):
    try:
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_ws()


def _validated_session_id(sid):
    try:
        return validate_session_id(sid), None
    except ValueError:
        return None, _invalid_sid()


# ─── Routes ───


def handle_session_create():
    """POST /api/sessions — Create a new session."""
    data = request.get_json(silent=True) or {}
    ws_id = data.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    title = (data.get("title") or "").strip()
    metadata = data.get("metadata") or {}

    session = create_session(ws_id=ws_id, title=title, metadata=metadata)
    return jsonify({"ok": True, "session": session})


def handle_session_list():
    """GET /api/sessions — List sessions for a workspace."""
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    status = request.args.get("status")
    if status == "":
        status = None
    limit = request.args.get("limit", "50")
    try:
        limit = int(limit)
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 500))

    sessions = list_sessions(ws_id, status=status, limit=limit)
    counts = get_session_count(ws_id)
    return jsonify({
        "ok": True,
        "sessions": sessions,
        "counts": counts,
        "workspace_id": ws_id,
    })


def handle_session_detail(session_id):
    """GET /api/sessions/<session_id> — Get session + messages."""
    session_id, err = _validated_session_id(session_id)
    if err:
        return err
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    session = get_session(session_id, ws_id)
    if not session:
        return jsonify({"ok": False, "error": "session_not_found"}), 404

    # Optionally include messages
    include_messages = request.args.get("include_messages", "1") == "1"
    result = {"ok": True, "session": session}
    if include_messages:
        result["messages"] = get_session_messages(session_id, ws_id)

    # v3.10: Attach session context from most recent run
    try:
        from storage.run_record_store import list_runs
        recent_runs = [r for r in list_runs(ws_id, limit=10) if r.get("session_id") == session_id]
        if recent_runs:
            latest = recent_runs[0]
            ctx = {}
            for key in ("capability", "intent", "runtime_mode", "llm_metadata"):
                if latest.get(key):
                    ctx[key] = latest[key]
            result["context"] = ctx
    except Exception:
        pass

    return jsonify(result)


def handle_session_update(session_id):
    """PUT /api/sessions/<session_id> — Update session metadata."""
    session_id, err = _validated_session_id(session_id)
    if err:
        return err
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    title = data.get("title")
    status = data.get("status")
    metadata = data.get("metadata")

    session = update_session(
        session_id, ws_id,
        title=title,
        status=status,
        metadata=metadata,
    )
    if not session:
        return jsonify({"ok": False, "error": "session_not_found"}), 404

    return jsonify({"ok": True, "session": session})


def _complete_session_job(ws_id, session_id, status="succeeded", hard_delete=False):
    """Mark the session-level agent_run job as completed/cancelled, or hard-delete it.

    IMPORTANT: This must NOT use list_jobs(), because list_jobs filters out
    jobs whose session no longer exists (_session_exists check). After
    soft/permanent deletion, the session is already gone, so list_jobs
    would never return the target job — creating orphan jobs.

    Instead, we scan the jobs directory directly.
    """
    import logging
    _log = logging.getLogger("session_routes.job_cleanup")
    try:
        from jobs.store import get_job, delete_job, _get_ws_root
        from jobs.manager import mark_succeeded, cancel_job

        jd = _get_ws_root() / ws_id / "jobs"
        if jd.is_dir():
            for entry in sorted(jd.iterdir()):
                if not entry.is_dir():
                    continue
                jf = entry / f"{entry.name}.json"
                if not jf.is_file():
                    continue
                j = get_job(ws_id, entry.name)
                if j and (j.payload or {}).get("session_id") == session_id and \
                   j.job_type == "agent_run":
                    if hard_delete:
                        delete_job(ws_id, j.job_id, soft=False)
                    elif status == "succeeded" and j.status not in ("succeeded",):
                        mark_succeeded(ws_id, j.job_id)
                    elif status == "cancelled" and j.status not in ("cancelled",):
                        cancel_job(ws_id, j.job_id)
                    break
    except Exception:
        _log.exception(
            "Failed to complete session job: ws=%s session=%s status=%s hard_delete=%s",
            ws_id, session_id, status, hard_delete,
        )


def handle_session_archive(session_id):
    """POST /api/sessions/<session_id>/archive — Archive a session."""
    session_id, err = _validated_session_id(session_id)
    if err:
        return err
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    session = archive_session(session_id, ws_id)
    if not session:
        return jsonify({"ok": False, "error": "session_not_found"}), 404

    _complete_session_job(ws_id, session_id, "succeeded")
    _schedule_session_memory_reflection(ws_id, session_id)

    return jsonify({"ok": True, "session": session})


def _schedule_session_memory_reflection(ws_id: str, session_id: str) -> None:
    """Flush the session's durable experience journal after archive."""
    import threading

    def run() -> None:
        try:
            from agent.runtime.memory_write.consolidator import consolidate_experiences

            consolidate_experiences(
                workspace_id=ws_id,
                session_id=session_id,
                task_id=f"session-{session_id}-archive",
            )
        except Exception:
            _log.warning(
                "session memory reflection failed: ws=%s session=%s",
                ws_id,
                session_id,
                exc_info=True,
            )

    threading.Thread(
        target=run,
        name=f"memory-reflect-{session_id[:16]}",
        daemon=True,
    ).start()


def handle_session_restore(session_id):
    """POST /api/sessions/<session_id>/restore — Restore an archived/deleted session."""
    session_id, err = _validated_session_id(session_id)
    if err:
        return err
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    session = update_session(session_id, ws_id, status="active")
    if not session:
        return jsonify({"ok": False, "error": "session_not_found"}), 404

    # Re-activate the job if it was completed/cancelled
    import logging
    _log = logging.getLogger("session_routes.restore")
    try:
        from jobs.store import get_job, list_jobs
        from jobs.manager import mark_running
        for j in list_jobs(ws_id=ws_id, limit=500):
            p = j.get("payload", {}) or {}
            if p.get("session_id") == session_id and j.get("status") in ("succeeded", "failed", "cancelled"):
                job_id = j.get("job_id", "")
                # Use state machine: mark_running handles succeeded→running
                # and cancelled→running transitions.
                # For failed jobs, retry_job→queued then mark_running.
                try:
                    rec = get_job(ws_id, job_id)
                    if rec and rec.status == "failed":
                        from jobs.manager import retry_job
                        retry_job(ws_id, job_id)
                    mark_running(ws_id, job_id)
                    # Clear finished_at to indicate active execution
                    from jobs.store import update_job
                    update_job(ws_id, job_id, {"finished_at": ""})
                except ValueError as e:
                    _log.warning("job reactivation failed job=%s status=%s: %s", job_id, j.get("status"), e)
                break
    except Exception:
        _log.exception("session restore job reactivation failed ws=%s session=%s", ws_id, session_id)

    return jsonify({"ok": True, "session": session})


def handle_session_soft_delete(session_id):
    """POST /api/sessions/<session_id>/soft-delete — Soft delete a session."""
    session_id, err = _validated_session_id(session_id)
    if err:
        return err
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    session = soft_delete_session(session_id, ws_id)
    if not session:
        return jsonify({"ok": False, "error": "session_not_found"}), 404

    _complete_session_job(ws_id, session_id, "cancelled")

    return jsonify({"ok": True, "session": session})


def handle_session_delete_permanently(session_id):
    """DELETE /api/sessions/<session_id> — Permanently delete session data.

    Requires ?confirm=true. Session metadata, messages, run records, traces,
    and decision sidecars are physically removed. Workspace artifacts remain.
    """
    session_id, err = _validated_session_id(session_id)
    if err:
        return err
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    confirm = request.args.get("confirm", "") == "true"
    if not confirm:
        return jsonify({
            "ok": False,
            "error": "confirm_required",
            "message": "Set ?confirm=true to permanently delete this session. Run records will be preserved.",
        }), 400

    ok = delete_session_permanently(session_id, ws_id, confirm=True)
    if not ok:
        return jsonify({"ok": False, "error": "session_not_found"}), 404

    _complete_session_job(ws_id, session_id, hard_delete=True)
    try:
        from agent.runtime.memory_write.event_log import delete_experience_journal

        delete_experience_journal(ws_id, session_id)
    except Exception:
        _log.warning(
            "session memory journal cleanup failed: ws=%s session=%s",
            ws_id,
            session_id,
            exc_info=True,
        )

    return jsonify({
        "ok": True,
        "message": "Session deleted. Run records and traces cleaned up. Artifacts retained for audit.",
    })


def handle_session_messages(session_id):
    """GET /api/sessions/<session_id>/messages — Get chat messages for a session."""
    session_id, err = _validated_session_id(session_id)
    if err:
        return err
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    messages = get_session_messages(session_id, ws_id)
    return jsonify({"ok": True, "messages": messages, "count": len(messages)})


def handle_session_default():
    """GET /api/sessions/default — Get or create the default active session."""
    ws_id = request.args.get("workspace_id", "")
    ws_id, err = _validated_ws_id(ws_id)
    if err:
        return err

    session = get_or_create_default_session(ws_id)
    return jsonify({"ok": True, "session": session})
