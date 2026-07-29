"""Session store — manage conversation sessions per workspace.

A Session is a conversation thread that groups multiple runs.
Sessions support soft-delete / archive semantics: deleting a session
only marks it as deleted; run records and artifacts remain intact
for audit purposes.
"""

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.ids import validate_session_id, validate_workspace_id
from storage.workspace_store import ensure_workspace
from storage.atomic_io import atomic_write_json

from storage.paths import get_workspace_root

def _ws_root() -> Path:
    return get_workspace_root()
_LOG = logging.getLogger(__name__)


def _session_dir(ws_id: str) -> Path:
    """Return the sessions directory for a workspace."""
    return _ws_root() / ws_id / "sessions"


def _session_path(session_id: str, ws_id: str) -> Path:
    """Return the file path for a session record. Validates session_id to prevent path traversal."""
    # Use the canonical storage validator so session validation
    # matches SessionMessageStore (rejects reserved names, >64 chars, etc.).
    safe_id = validate_session_id(session_id)
    return _session_dir(ws_id) / f"{safe_id}.json"


def _now_iso() -> str:
    """Return current UTC time in ISO format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ─── Session CRUD ───


def create_session(
    ws_id: str = "default",
    title: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new session. Returns the session dict."""
    ws_id = ensure_workspace(ws_id)
    _session_dir(ws_id).mkdir(parents=True, exist_ok=True)

    session_id = uuid.uuid4().hex[:16]
    now = _now_iso()
    session = {
        "session_id": session_id,
        "workspace_id": ws_id,
        "title": title or "新会话",
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "run_ids": [],
        "metadata": metadata or {},
    }
    _write_session(session, ws_id)
    return session


def get_session(session_id: str, ws_id: str = "default") -> Optional[Dict[str, Any]]:
    """Get a single session by ID."""
    ws_id = validate_workspace_id(ws_id)
    path = _session_path(session_id, ws_id)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            _LOG.warning("session_store: silent exception", exc_info=True)
    return None


def list_sessions(
    ws_id: str = "default",
    status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List sessions for a workspace.

    Args:
        ws_id: Workspace ID.
        status: Filter by status ('active', 'archived', 'deleted').
                None means include all non-deleted (active + archived).
        limit: Max number of sessions to return.

    v3.1.1: Auto-repairs orphaned session directories (messages on disk
    but no JSON metadata) by synthesizing minimal session metadata.
    """
    ws_id = ensure_workspace(ws_id)
    sdir = _session_dir(ws_id)
    if not sdir.is_dir():
        return []

    sessions = []
    seen_ids = set()

    for f in sdir.glob("*.json"):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
            sessions.append(s)
            seen_ids.add(s.get("session_id", f.stem))
        except Exception:
            _LOG.warning("session_store: silent exception", exc_info=True)

    # v3.1.1: Auto-repair orphaned sessions (directories with messages but no .json)
    for item in sdir.iterdir():
        if not item.is_dir():
            continue
        sid = item.name
        if sid in seen_ids:
            continue
        msg_dir = item / "messages"
        if not msg_dir.is_dir():
            continue
        try:
            msgs = sorted(msg_dir.iterdir())
            if not msgs:
                continue
            # Derive title from first user message
            title = sid
            first_ts = _now_iso()
            for mf in msgs:
                if mf.name.endswith(":user.json"):
                    try:
                        data = json.loads(mf.read_text(encoding="utf-8"))
                        content = data.get("content", "")
                        if content:
                            title = content[:60].replace("\n", " ")
                        first_ts = data.get("timestamp", first_ts)
                    except Exception:
                        _LOG.warning("session_store: silent exception", exc_info=True)
                    break
            session_data = {
                "session_id": sid, "workspace_id": ws_id,
                "title": title, "status": "active",
                "created_at": first_ts, "updated_at": first_ts,
                "run_ids": [], "metadata": {"auto_repaired": True},
            }
            _write_session(session_data, ws_id)
            sessions.append(session_data)
            seen_ids.add(sid)
        except Exception:
            _LOG.warning("session_store: silent exception", exc_info=True)

    sessions = [s for s in sessions if not _is_internal_session(s)]

    # Default filter: exclude deleted
    if status is None:
        sessions = [s for s in sessions if s.get("status") != "deleted"]
    else:
        sessions = [s for s in sessions if s.get("status") == status]

    # Sort by updated_at desc
    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions[:limit]


def update_session(
    session_id: str,
    ws_id: str = "default",
    title: Optional[str] = None,
    status: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Update session fields. Returns updated session or None if not found."""
    ws_id = validate_workspace_id(ws_id)
    session = get_session(session_id, ws_id)
    if not session:
        return None

    if title is not None:
        session["title"] = title
    if status is not None:
        if status in ("active", "archived", "deleted"):
            session["status"] = status
    if metadata is not None:
        session["metadata"] = metadata

    session["updated_at"] = _now_iso()
    _write_session(session, ws_id)
    return session


def archive_session(session_id: str, ws_id: str = "default") -> Optional[Dict[str, Any]]:
    """Soft-archive a session (status → 'archived')."""
    return update_session(session_id, ws_id, status="archived")


def soft_delete_session(session_id: str, ws_id: str = "default") -> Optional[Dict[str, Any]]:
    """Soft-delete a session (status → 'deleted'). Run records are preserved."""
    return update_session(session_id, ws_id, status="deleted")


def delete_session_permanently(
    session_id: str, ws_id: str = "default", confirm: bool = False
) -> bool:
    """Physically delete the session and its messages.

    Removes both the JSON metadata file and the messages/ directory
    so that list_sessions' auto-repair does not resurrect the session.

    Also cascades to clean up associated run records and trace files
    to prevent orphan data. Artifacts are left intact for audit purposes
    (they are workspace-scoped, not session-scoped).

    Requires confirm=True as a safety guard.
    """
    if not confirm:
        return False
    import shutil
    import logging
    _log = logging.getLogger("session_store.delete")
    ws_id = validate_workspace_id(ws_id)

    # ── Collect run_ids before deletion ──
    session = get_session(session_id, ws_id)
    run_ids = list((session or {}).get("run_ids", []))

    # Also scan runs dir for any runs with this session_id (recovery)
    try:
        from storage.run_record_store import list_runs
        for run in list_runs(ws_id, limit=5000):
            if run.get("session_id") == session_id:
                rid = run.get("run_id") or run.get("turn_id") or ""
                if rid and rid not in run_ids:
                    run_ids.append(rid)
    except Exception:
        _log.debug("run scan failed for session=%s ws=%s", session_id, ws_id)

    # ── Delete run records and trace files ──
    runs_dir = _ws_root() / ws_id / "runs"
    for rid in run_ids:
        # Delete run record
        run_file = runs_dir / f"{rid}.json"
        if run_file.is_file():
            try:
                run_file.unlink()
            except Exception:
                _log.debug("failed to delete run file: %s", run_file)

        # Delete trace sidecar
        trace_file = runs_dir / f"{rid}.trace.json"
        if trace_file.is_file():
            try:
                trace_file.unlink()
            except Exception:
                _log.debug("failed to delete trace file: %s", trace_file)

        # Delete decision sidecar
        decision_file = runs_dir / f"{rid}.decision.json"
        if decision_file.is_file():
            try:
                decision_file.unlink()
            except Exception:
                _log.debug("failed to delete decision file: %s", decision_file)

    _log.info("cascaded delete: session=%s ws=%s runs_deleted=%d", session_id, ws_id, len(run_ids))

    # ── Delete session metadata and messages ──
    path = _session_path(session_id, ws_id)
    msg_dir = _session_dir(ws_id) / str(session_id)

    deleted = False
    if path.is_file():
        try:
            path.unlink()
            deleted = True
        except Exception:
            _LOG.warning("session_store: silent exception", exc_info=True)

    # Also remove the messages directory to prevent auto-repair resurrection
    if msg_dir.is_dir():
        try:
            shutil.rmtree(msg_dir)
            deleted = True
        except Exception:
            _LOG.warning("session_store: silent exception", exc_info=True)

    return deleted


# ─── Run association ───


def add_run_to_session(
    session_id: str, run_id: str, ws_id: str = "default"
) -> Optional[Dict[str, Any]]:
    """Append a run_id to a session's run_ids list."""
    ws_id = validate_workspace_id(ws_id)
    session = get_session(session_id, ws_id)
    if not session:
        return None

    run_ids = session.get("run_ids", [])
    if run_id not in run_ids:
        run_ids.append(run_id)
        session["run_ids"] = run_ids
        session["updated_at"] = _now_iso()
        # Auto-title: use first user input as session name
        if not session.get("title"):
            title = _auto_title_from_run(run_id, ws_id)
            if title:
                session["title"] = title
        _write_session(session, ws_id)
    return session


def _auto_title_from_run(run_id: str, ws_id: str) -> str:
    """Generate a human-friendly title from the run's user input."""
    try:
        from storage.run_record_store import get_run
        run = get_run(run_id, ws_id)
        if run:
            text = (run.get("user_input_summary") or "").strip()
            if text and len(text) > 3:
                return text[:40] + ("..." if len(text) > 40 else "")
    except Exception:
        _LOG.warning("session_store: silent exception", exc_info=True)
    return ""


def get_session_messages(session_id: str, ws_id: str = "default") -> List[Dict[str, Any]]:
    """Return a session's messages for chat UI restoration.

    Full message files are canonical for current runs. Older or interrupted
    runs may have a valid session association but no message files; in that
    case, project the sanitized run summaries into chat messages. Missing or
    deleted sessions never fall back to runs, so deletion semantics remain
    intact.
    """
    from storage.message_store import SessionMessageStore

    session = get_session(session_id, ws_id)
    if session is None:
        return []
    if _is_internal_session(session):
        return []

    store = SessionMessageStore(session_id=session_id, ws_id=ws_id)
    messages = store.get_messages()

    from storage.run_record_store import list_runs, run_sort_key

    runs = [
        run for run in list_runs(ws_id, limit=100_000)
        if run.get("session_id") == session_id
    ]
    runs.sort(key=run_sort_key)

    projected: List[Dict[str, Any]] = []
    for run in runs:
        run_id = str(run.get("run_id") or run.get("turn_id") or "").strip()
        if not run_id:
            continue
        created_at = (
            run.get("created_at")
            or run.get("started_at")
            or run.get("finished_at")
            or ""
        )
        metadata = {
            key: run[key]
            for key in (
                "intent",
                "status",
                "capability",
                "quality_summary",
                "manual_review_count",
                "trace_id",
                "llm_metadata",
            )
            if key in run
        }
        user_content = str(run.get("user_input_summary") or "").strip()
        assistant_content = str(run.get("final_response_summary") or "").strip()
        if not assistant_content:
            assistant_content = _tool_only_assistant_projection(ws_id, run_id)
        if user_content:
            projected.append({
                "message_id": f"{run_id}:user",
                "session_id": session_id,
                "role": "user",
                "content": user_content,
                "created_at": created_at,
                "run_id": run_id,
                "metadata": metadata,
            })
        if assistant_content:
            projected.append({
                "message_id": f"{run_id}:assistant",
                "session_id": session_id,
                "role": "assistant",
                "content": assistant_content,
                "created_at": created_at,
                "run_id": run_id,
                "metadata": metadata,
            })
    if not messages:
        return projected

    existing_ids = {str(m.get("message_id") or "") for m in messages}
    merged = list(messages)
    merged.extend(m for m in projected if m.get("message_id") not in existing_ids)
    try:
        from storage.message_store import _message_sort_key
        merged.sort(key=_message_sort_key)
    except Exception:
        merged.sort(key=lambda m: (
            m.get("created_at") or "",
            m.get("run_id") or "",
            0 if m.get("role") == "user" else 1,
            m.get("message_id") or "",
        ))
    return merged


def _tool_only_assistant_projection(ws_id: str, run_id: str) -> str:
    """Build a readable assistant message for historical tool-only turns."""
    try:
        decision_path = _ws_root() / ws_id / "runs" / f"{run_id}.decision.json"
        if decision_path.is_file():
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            summary = decision.get("tool_execution_summary") or {}
            if isinstance(summary, dict):
                called = [str(x) for x in (summary.get("called") or []) if x]
                succeeded = [str(x) for x in (summary.get("succeeded") or []) if x]
                failed = [str(x) for x in (summary.get("failed") or []) if x]
                blocked = [str(x) for x in (summary.get("blocked") or []) if x]
                if called:
                    lines = [
                        f"工具调用已完成：共 {len(called)} 次，成功 {len(succeeded)} 次，失败 {len(failed)} 次，阻止 {len(blocked)} 次。"
                    ]
                    for idx, tool_id in enumerate(called[:8], start=1):
                        if tool_id in failed:
                            status = "失败"
                        elif tool_id in blocked:
                            status = "阻止"
                        elif tool_id in succeeded:
                            status = "成功"
                        else:
                            status = "已调用"
                        lines.append(f"{idx}. {tool_id} {status}")
                    if len(called) > 8:
                        lines.append(f"... 另有 {len(called) - 8} 次工具调用已省略。")
                    return "\n".join(lines)
    except Exception:
        _LOG.debug("session_store: tool-only assistant projection failed",
                   exc_info=True)
    return ""


def get_or_create_default_session(ws_id: str = "default") -> Dict[str, Any]:
    """Get the most recent active session, or create one if none exists."""
    sessions = list_sessions(ws_id, status="active", limit=1)
    if sessions:
        return sessions[0]
    return create_session(ws_id, title="默认会话")


def auto_title_from_input(session_id: str, user_input: str, ws_id: str = "default") -> Optional[str]:
    """Auto-generate a session title from the first user input if the title is generic.

    Returns the new title if updated, None otherwise.
    """
    ws_id = validate_workspace_id(ws_id)
    session = get_session(session_id, ws_id)
    if not session:
        return None

    current_title = session.get("title", "")
    # Only auto-title if current title is generic
    if current_title not in ("新会话", "默认会话", ""):
        return None

    # Use first 20 chars of user input as title
    title = user_input.strip()
    if len(title) > 20:
        title = title[:20] + "..."
    if not title:
        return None

    update_session(session_id, ws_id, title=title)
    return title


# ─── Internal helpers ───


def _write_session(session: Dict[str, Any], ws_id: str):
    """Persist session to disk atomically to prevent corruption on concurrent writes."""
    path = _session_path(session["session_id"], ws_id)
    atomic_write_json(path, session)


# ─── Cleanup helpers ───


def list_sessions_by_status(ws_id: str = "default") -> Dict[str, List[Dict[str, Any]]]:
    """Return sessions grouped by status."""
    ws_id = ensure_workspace(ws_id)
    all_sessions = []
    sdir = _session_dir(ws_id)
    if sdir.is_dir():
        for f in sdir.glob("*.json"):
            try:
                session = json.loads(f.read_text(encoding="utf-8"))
                if not _is_internal_session(session):
                    all_sessions.append(session)
            except Exception:
                _LOG.warning("session_store: silent exception", exc_info=True)

    return {
        "active": [s for s in all_sessions if s.get("status") == "active"],
        "archived": [s for s in all_sessions if s.get("status") == "archived"],
        "deleted": [s for s in all_sessions if s.get("status") == "deleted"],
    }


def get_session_count(ws_id: str = "default") -> Dict[str, int]:
    """Return counts of sessions by status."""
    grouped = list_sessions_by_status(ws_id)
    return {
        "active": len(grouped["active"]),
        "archived": len(grouped["archived"]),
        "deleted": len(grouped["deleted"]),
        "total": len(grouped["active"]) + len(grouped["archived"]) + len(grouped["deleted"]),
    }


def _is_internal_session(session: Dict[str, Any]) -> bool:
    """Return True for runtime-owned sessions that should not appear in chat UI."""
    sid = str(session.get("session_id") or "")
    title = str(session.get("title") or "")
    metadata = session.get("metadata") or {}
    if sid.startswith("sub-"):
        return True
    if title.startswith("You are a subagent:"):
        return True
    if isinstance(metadata, dict) and (
        metadata.get("internal")
        or metadata.get("is_subagent")
        or metadata.get("subtask_id")
        or metadata.get("parent_session_id")
    ):
        return True
    return False
