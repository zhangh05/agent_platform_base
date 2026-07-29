"""Session Snapshot & Rewind — save and restore session message state.

Snapshots save the current messages/history of a session to a JSON file.
Rewind restores messages from a snapshot, optionally in dry_run mode
that previews what would happen without making changes.
"""

import uuid
from typing import Any, Dict, List, Optional

from storage.ids import validate_session_id, validate_workspace_id
from storage.records import atomic_save_json, list_json_records, read_json_record


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    """Sanitize an ID to prevent path traversal."""
    import re
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(value))
    if not safe:
        raise ValueError(f"Invalid id: {value!r}")
    return safe


def create_snapshot(workspace_id: str, session_id: str, reason: str = "") -> dict:
    """Create a snapshot of the current session messages.

    Args:
        workspace_id: Workspace identifier.
        session_id: Session identifier.
        reason: Optional human-readable reason for the snapshot.

    Returns:
        {"ok": True, "snapshot_id": "...", "message_count": N}
    """
    try:
        ws_id = validate_workspace_id(workspace_id)
        sid = validate_session_id(session_id)

        # Read current session messages
        from storage.session_store import get_session
        session = get_session(sid, ws_id)
        if not session:
            return {"ok": False, "error": "session not found"}

        from storage.message_store import SessionMessageStore
        store = SessionMessageStore(session_id=sid, ws_id=ws_id)
        messages = store.get_messages()

        snap_id = uuid.uuid4().hex[:12]
        snapshot = {
            "snapshot_id": snap_id,
            "workspace_id": ws_id,
            "session_id": sid,
            "reason": reason or "",
            "message_count": len(messages),
            "created_at": _now_iso(),
            "messages": messages,
        }

        atomic_save_json(ws_id, ("sessions", sid, "snapshots", f"{snap_id}.json"), snapshot)

        return {
            "ok": True,
            "snapshot_id": snap_id,
            "message_count": len(messages),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def list_snapshots(workspace_id: str, session_id: str) -> list:
    """List all snapshots for a session (without full message content).

    Args:
        workspace_id: Workspace identifier.
        session_id: Session identifier.

    Returns:
        List of snapshot summary dicts.
    """
    try:
        ws_id = validate_workspace_id(workspace_id)
        sid = validate_session_id(session_id)

        results = []
        for data in list_json_records(ws_id, ("sessions", sid, "snapshots"), limit=500):
            try:
                results.append({
                    "snapshot_id": data.get("snapshot_id", ""),
                    "reason": data.get("reason", ""),
                    "message_count": data.get("message_count", 0),
                    "created_at": data.get("created_at", ""),
                })
            except Exception:
                continue

        return results
    except Exception:
        return []


def rewind_session(workspace_id: str, session_id: str,
                   snapshot_id: str, dry_run: bool = True) -> dict:
    """Rewind a session to a previous snapshot state.

    Args:
        workspace_id: Workspace identifier.
        session_id: Session identifier.
        snapshot_id: Snapshot ID to restore from.
        dry_run: If True, only preview what would happen without applying.

    Returns:
        {"ok": True, "message_count": N, "dry_run": bool}
    """
    try:
        ws_id = validate_workspace_id(workspace_id)
        sid = validate_session_id(session_id)
        snap_id = _safe_id(snapshot_id)

        # Load snapshot
        snapshot = read_json_record(ws_id, ("sessions", sid, "snapshots", f"{snap_id}.json"))
        if not snapshot:
            return {"ok": False, "error": f"snapshot not found: {snap_id}"}

        messages = snapshot.get("messages", [])

        if dry_run:
            return {
                "ok": True,
                "message_count": len(messages),
                "dry_run": True,
                "preview": {
                    "snapshot_id": snap_id,
                    "reason": snapshot.get("reason", ""),
                    "created_at": snapshot.get("created_at", ""),
                    "message_count": len(messages),
                    "action": "Would restore session messages to this snapshot state. Set dry_run=False to apply.",
                },
            }

        # ── Apply: restore messages ──
        from storage.message_store import SessionMessageStore
        store = SessionMessageStore(session_id=sid, ws_id=ws_id)

        # Clear existing message files
        msg_dir = store._messages_dir()
        if msg_dir.is_dir():
            for f in msg_dir.glob("*.json"):
                try:
                    f.unlink()
                except Exception:
                    pass

        # Write restored messages
        msg_dir.mkdir(parents=True, exist_ok=True)
        for msg in messages:
            run_id = msg.get("run_id", "")
            role = msg.get("role", "")
            content = msg.get("content", "")
            if run_id and role and content:
                store.write_message(
                    run_id=run_id,
                    role=role,
                    content=content,
                    metadata=msg.get("metadata"),
                )

        return {
            "ok": True,
            "message_count": len(messages),
            "dry_run": False,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
