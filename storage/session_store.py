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
from storage.locking import FileLock

from storage.paths import workspace_root

def _ws_root(ws_id: str) -> Path:
    return workspace_root(validate_workspace_id(ws_id))
_LOG = logging.getLogger(__name__)


def _session_dir(ws_id: str) -> Path:
    """Return the sessions directory for a workspace."""
    return _ws_root(ws_id) / "sessions"


def _session_path(session_id: str, ws_id: str) -> Path:
    """Return the file path for a session record. Validates session_id to prevent path traversal."""
    # Use the canonical storage validator so session validation
    # matches SessionMessageStore (rejects reserved names, >64 chars, etc.).
    safe_id = validate_session_id(session_id)
    return _session_dir(ws_id) / f"{safe_id}.json"



def _session_lock_path(session_id: str, ws_id: str) -> Path:
    """Return a stable lock path that is never atomically replaced."""
    session_path = _session_path(session_id, ws_id)
    return session_path.with_name(f".{session_path.stem}.lock")


def _session_lock(session_id: str, ws_id: str) -> FileLock:
    return FileLock(_session_lock_path(session_id, ws_id))


def _read_session_unlocked(session_id: str, ws_id: str) -> Optional[Dict[str, Any]]:
    path = _session_path(session_id, ws_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        _LOG.warning(
            "session_read_failed action=read session_id=%s workspace_id=%s error_type=%s",
            session_id,
            ws_id,
            type(exc).__name__,
        )
        return None
    return value if isinstance(value, dict) else None


def _write_session_unlocked(session: Dict[str, Any], ws_id: str) -> None:
    atomic_write_json(_session_path(session["session_id"], ws_id), session)


def _session_tombstone_path(session_id: str, ws_id: str) -> Path:
    session_path = _session_path(session_id, ws_id)
    return session_path.with_name(f".{session_path.stem}.deleted")


def _session_is_tombstoned_unlocked(session_id: str, ws_id: str) -> bool:
    return _session_tombstone_path(session_id, ws_id).is_file()


def _mark_session_deleted_unlocked(session_id: str, ws_id: str) -> None:
    atomic_write_json(
        _session_tombstone_path(session_id, ws_id),
        {"session_id": session_id, "workspace_id": ws_id, "deleted_at": _now_iso()},
    )


def _mutate_session(session_id: str, ws_id: str, mutator) -> Optional[Dict[str, Any]]:
    """Run one complete session read/validate/mutate/write transaction."""
    safe_id = validate_session_id(session_id)
    ws_id = validate_workspace_id(ws_id)
    with _session_lock(safe_id, ws_id):
        if _session_is_tombstoned_unlocked(safe_id, ws_id):
            return None
        session = _read_session_unlocked(safe_id, ws_id)
        if not session:
            return None
        if mutator(session):
            session["updated_at"] = _now_iso()
            _write_session_unlocked(session, ws_id)
        return session

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


def ensure_session(
    session_id: str,
    ws_id: str = "default",
    *,
    title: str = "新会话",
    created_at: str = "",
) -> Dict[str, Any]:
    """Return an existing session or create one inside the session lock."""
    ws_id = ensure_workspace(ws_id)
    safe_id = validate_session_id(session_id)
    with _session_lock(safe_id, ws_id):
        if _session_is_tombstoned_unlocked(safe_id, ws_id):
            raise ValueError("session permanently deleted")
        existing = _read_session_unlocked(safe_id, ws_id)
        if existing:
            return existing
        now = created_at or _now_iso()
        session = {
            "session_id": safe_id,
            "workspace_id": ws_id,
            "title": title or "新会话",
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "run_ids": [],
            "metadata": {},
        }
        _write_session_unlocked(session, ws_id)
        return session


def get_session(session_id: str, ws_id: str = "default") -> Optional[Dict[str, Any]]:
    """Read one complete session record under its stable lock."""
    ws_id = validate_workspace_id(ws_id)
    safe_id = validate_session_id(session_id)
    with _session_lock(safe_id, ws_id):
        if _session_is_tombstoned_unlocked(safe_id, ws_id):
            return None
        return _read_session_unlocked(safe_id, ws_id)

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
            sid = s.get("session_id", f.stem)
            metadata = s.get("metadata") or {}
            if (
                (metadata.get("auto_repaired") and not metadata.get("repair_complete"))
                or s.get("title") == sid
            ):
                repaired = _session_from_messages(sid, ws_id, base=s)
                if repaired:
                    s = repaired
            sessions.append(s)
            seen_ids.add(sid)
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
            session_data = _session_from_messages(sid, ws_id)
            if not session_data:
                continue
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


def _session_from_messages(
    session_id: str,
    ws_id: str,
    *,
    base: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Rebuild session title, timestamps, and run ids from canonical messages."""
    msg_dir = _session_dir(ws_id) / session_id / "messages"
    if not msg_dir.is_dir():
        return None
    message_files = sorted(msg_dir.glob("*.json"))
    if not message_files:
        return None

    records: list[tuple[str, str, Dict[str, Any]]] = []
    for message_file in message_files:
        try:
            data = json.loads(message_file.read_text(encoding="utf-8"))
        except Exception:
            _LOG.warning("session message repair read failed: %s", message_file, exc_info=True)
            continue
        role = str(data.get("role") or "")
        timestamp = str(
            (data.get("metadata") or {}).get("created_at")
            or data.get("created_at")
            or data.get("timestamp")
            or ""
        )
        records.append((timestamp, role, data))
    if not records:
        return None
    records.sort(key=lambda item: item[0])
    run_ids: list[str] = []
    for _, _, data in records:
        run_id = str(data.get("run_id") or "").strip()
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)
    timestamps = [item[0] for item in records if item[0]]
    title = ""
    for _, role, data in records:
        if role == "user" and str(data.get("content") or "").strip():
            title = str(data["content"]).strip().replace("\n", " ")[:60]
            break
    safe_id = validate_session_id(session_id)
    with _session_lock(safe_id, ws_id):
        if _session_is_tombstoned_unlocked(safe_id, ws_id):
            return None
        current = _read_session_unlocked(safe_id, ws_id)
        session = dict(current or base or {})
        merged_run_ids = list(session.get("run_ids") or [])
        for run_id in run_ids:
            if run_id not in merged_run_ids:
                merged_run_ids.append(run_id)
        session.update({
            "session_id": safe_id,
            "workspace_id": ws_id,
            "title": title or session.get("title") or "新会话",
            "status": session.get("status") or "active",
            "created_at": session.get("created_at") or (min(timestamps) if timestamps else _now_iso()),
            "updated_at": max(str(session.get("updated_at") or ""), max(timestamps, default="")) or _now_iso(),
            "run_ids": merged_run_ids,
            "metadata": {**dict(session.get("metadata") or {}), "auto_repaired": True, "repair_complete": True},
        })
        _write_session_unlocked(session, ws_id)
        return session

def update_session(
    session_id: str,
    ws_id: str = "default",
    title: Optional[str] = None,
    status: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Update fields in one locked read-modify-write transaction."""
    ws_id = validate_workspace_id(ws_id)

    def _apply(session: Dict[str, Any]) -> bool:
        changed = False
        if title is not None and session.get("title") != title:
            session["title"] = title
            changed = True
        if status in ("active", "archived", "deleted") and session.get("status") != status:
            session["status"] = status
            changed = True
        if metadata is not None:
            merged = {**dict(session.get("metadata") or {}), **dict(metadata)}
            if session.get("metadata") != merged:
                session["metadata"] = merged
                changed = True
        return changed

    return _mutate_session(session_id, ws_id, _apply)
def archive_session(session_id: str, ws_id: str = "default") -> Optional[Dict[str, Any]]:
    """Soft-archive a session (status → 'archived')."""
    return update_session(session_id, ws_id, status="archived")


def soft_delete_session(session_id: str, ws_id: str = "default") -> Optional[Dict[str, Any]]:
    """Soft-delete a session (status → 'deleted'). Run records are preserved."""
    return update_session(session_id, ws_id, status="deleted")


def delete_session_permanently(
    session_id: str, ws_id: str = "default", confirm: bool = False
) -> bool:
    """Delete session state under one lock and leave a tombstone against revival."""
    if not confirm:
        return False
    import shutil

    ws_id = validate_workspace_id(ws_id)
    safe_id = validate_session_id(session_id)
    with _session_lock(safe_id, ws_id):
        session = _read_session_unlocked(safe_id, ws_id)
        path = _session_path(safe_id, ws_id)
        msg_dir = _session_dir(ws_id) / safe_id
        request_registry_dir = _ws_root(ws_id) / "sys" / "request_registry" / safe_id
        had_data = bool(session or path.is_file() or msg_dir.is_dir() or request_registry_dir.is_dir())
        if not _session_is_tombstoned_unlocked(safe_id, ws_id):
            _mark_session_deleted_unlocked(safe_id, ws_id)
        failures: list[str] = []
        run_ids = list((session or {}).get("run_ids", []))
        try:
            from storage.run_record_store import list_runs
            for run in list_runs(ws_id, limit=5000):
                if run.get("session_id") == safe_id:
                    run_id = str(run.get("run_id") or run.get("turn_id") or "")
                    if run_id and run_id not in run_ids:
                        run_ids.append(run_id)
        except (OSError, TypeError, ValueError) as exc:
            failures.append("run_scan_failed")
            _LOG.warning(
                "session_delete_scan_failed action=delete session_id=%s workspace_id=%s error_type=%s",
                safe_id,
                ws_id,
                type(exc).__name__,
            )
        runs_dir = _ws_root(ws_id) / "runs"
        for run_id in run_ids:
            for suffix in (".json", ".trace.json", ".decision.json"):
                record_path = runs_dir / f"{run_id}{suffix}"
                if record_path.is_file():
                    try:
                        record_path.unlink()
                    except OSError as exc:
                        failures.append(f"run_delete_failed:{record_path.name}")
                        _LOG.warning(
                            "session_delete_run_failed action=delete session_id=%s workspace_id=%s object_id=%s error_type=%s",
                            safe_id,
                            ws_id,
                            record_path.name,
                            type(exc).__name__,
                        )
        if path.is_file():
            try:
                path.unlink()
            except OSError as exc:
                failures.append("metadata_delete_failed")
                _LOG.warning(
                    "session_delete_metadata_failed action=delete session_id=%s workspace_id=%s error_type=%s",
                    safe_id,
                    ws_id,
                    type(exc).__name__,
                )
        if msg_dir.is_dir():
            try:
                shutil.rmtree(msg_dir)
            except OSError as exc:
                failures.append("messages_delete_failed")
                _LOG.warning(
                    "session_delete_messages_failed action=delete session_id=%s workspace_id=%s error_type=%s",
                    safe_id,
                    ws_id,
                    type(exc).__name__,
                )
        if request_registry_dir.is_dir():
            try:
                shutil.rmtree(request_registry_dir)
            except OSError as exc:
                failures.append("request_registry_delete_failed")
                _LOG.warning(
                    "session_delete_request_registry_failed action=delete session_id=%s workspace_id=%s error_type=%s",
                    safe_id,
                    ws_id,
                    type(exc).__name__,
                )
        complete = not path.exists() and not msg_dir.exists() and not request_registry_dir.exists()
        if failures or not complete:
            _LOG.error(
                "session_hard_delete_incomplete session_id=%s workspace_id=%s failures=%s",
                safe_id,
                ws_id,
                failures or ["residual_paths"],
            )
            return False
        return had_data


# ─── Run association ───


def add_run_to_session(
    session_id: str, run_id: str, ws_id: str = "default"
) -> Optional[Dict[str, Any]]:
    """Append one run id in the same transaction used by other session updates."""
    ws_id = validate_workspace_id(ws_id)

    def _apply(session: Dict[str, Any]) -> bool:
        run_ids = list(session.get("run_ids") or [])
        if run_id in run_ids:
            return False
        run_ids.append(run_id)
        session["run_ids"] = run_ids
        if not session.get("title"):
            title = _auto_title_from_run(run_id, ws_id)
            if title:
                session["title"] = title
        return True

    return _mutate_session(session_id, ws_id, _apply)


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
                "client_request_id",
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
    existing_client_request_ids = {
        str((message.get("metadata") or {}).get("client_request_id") or "")
        for message in messages
        if message.get("role") == "user"
    }
    merged = list(messages)
    merged.extend(
        message for message in projected
        if message.get("message_id") not in existing_ids
        and not (
            message.get("role") == "user"
            and str((message.get("metadata") or {}).get("client_request_id") or "")
            and str((message.get("metadata") or {}).get("client_request_id") or "")
            in existing_client_request_ids
        )
    )
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
        decision_path = _ws_root(ws_id) / "runs" / f"{run_id}.decision.json"
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
    """Persist a session under its stable lock; transactions use the unlocked helper."""
    safe_id = validate_session_id(session["session_id"])
    ws_id = validate_workspace_id(ws_id)
    with _session_lock(safe_id, ws_id):
        if _session_is_tombstoned_unlocked(safe_id, ws_id):
            raise ValueError("session permanently deleted")
        _write_session_unlocked(session, ws_id)


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
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                _LOG.warning(
                    "session_list_failed action=list workspace_id=%s object_id=%s error_type=%s",
                    ws_id,
                    f.name,
                    type(exc).__name__,
                )
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
