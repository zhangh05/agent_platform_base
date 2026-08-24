from __future__ import annotations

from agent.runtime.utils import now_iso
from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id

from core.tools.general_tools.shared import _caller_workspace, _error_inv, _ok, _result
"""Split general tool handlers."""

def handle_session_get_summary(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    sid = inv.arguments.get("session_id", "")
    try:
        validate_workspace_id(ws)
        from storage.session_store import get_session
        s = get_session(sid, ws)
        if not s:
            return _error_inv(inv, "session not found")
        messages = s.get("messages", [])
        return _ok(inv, "", {
            "session_id": sid,
            "title": s.get("title", ""),
            "message_count": len(messages),
            "first_message": messages[0].get("content", "")[:100] if messages else "",
            "last_message": messages[-1].get("content", "")[:100] if messages else "",
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_session_create(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    title = inv.arguments.get("title", "new_session")
    try:
        validate_workspace_id(ws)
        from storage.session_store import create_session
        session = create_session(ws_id=ws, title=title)
        return _ok(inv, "", {
            "session_id": session.get("session_id", ""),
            "title": session.get("title", title),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_session_archive(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    sid = inv.arguments.get("session_id", "")
    try:
        validate_workspace_id(ws)
        from storage.session_store import archive_session
        archived = archive_session(sid, ws)
        return _ok(inv, "", {"archived": True}) if archived else _error_inv(inv, "archive failed")
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_run_get_summary(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    run_id = inv.arguments.get("run_id", "")
    try:
        validate_workspace_id(ws)
        from storage.run_record_store import get_run
        r = get_run(run_id, ws)
        if not r:
            return _error_inv(inv, "run not found")
        return _ok(inv, "", {
            "run_id": run_id,
            "intent": r.get("intent", ""),
            "status": r.get("status", "ok"),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_session_snapshot(inv: ToolInvocation) -> dict:
    """Create a snapshot of the current session state."""
    ws = _caller_workspace(inv)
    sid = inv.arguments.get("session_id", "")
    reason = str(inv.arguments.get("reason", "")).strip()
    if not sid:
        return _error_inv(inv, "session_id is required")
    try:
        validate_workspace_id(ws)
        from storage.session_snapshot import create_snapshot
        result = create_snapshot(workspace_id=ws, session_id=sid, reason=reason)
        return _result(inv, result.get("ok", False), result)
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_session_list_snapshots(inv: ToolInvocation) -> dict:
    """List snapshots for a session."""
    ws = _caller_workspace(inv)
    sid = inv.arguments.get("session_id", "")
    if not sid:
        return _error_inv(inv, "session_id is required")
    try:
        validate_workspace_id(ws)
        from storage.session_snapshot import list_snapshots
        results = list_snapshots(workspace_id=ws, session_id=sid)
        return _ok(inv, "", {"snapshots": results, "count": len(results)})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_session_rewind(inv: ToolInvocation) -> dict:
    """Rewind a session to a previous snapshot."""
    ws = _caller_workspace(inv)
    sid = inv.arguments.get("session_id", "")
    snap_id = inv.arguments.get("snapshot_id", "")
    dry_run = bool(inv.arguments.get("dry_run", True))
    if not sid:
        return _error_inv(inv, "session_id is required")
    if not snap_id:
        return _error_inv(inv, "snapshot_id is required")
    try:
        validate_workspace_id(ws)
        from storage.session_snapshot import rewind_session
        result = rewind_session(
            workspace_id=ws,
            session_id=sid,
            snapshot_id=snap_id,
            dry_run=dry_run,
        )
        return _result(inv, result.get("ok", False), result)
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_session_checkpoint(inv: ToolInvocation) -> dict:
    """Create a checkpoint with message/run/artifact references."""
    ws = _caller_workspace(inv)
    sid = inv.arguments.get("session_id", "")
    reason = str(inv.arguments.get("reason", "")).strip()
    if not sid:
        return _error_inv(inv, "session_id is required")
    try:
        validate_workspace_id(ws)
        from storage.session_store import get_session
        s = get_session(sid, ws)
        if not s:
            return _error_inv(inv, "session not found")
        import uuid
        cid = str(uuid.uuid4())[:8]
        messages = s.get("messages", [])
        checkpoint = {
            "checkpoint_id": cid,
            "session_id": sid,
            "workspace_id": ws,
            "reason": reason,
            "message_count": len(messages),
            "run_refs": s.get("run_refs", []),
            "artifact_refs": s.get("artifact_refs", []),
            "created_at": now_iso(),
        }
        from storage.session_checkpoint_store import save_checkpoint
        save_checkpoint(ws, sid, cid, checkpoint)
        return _ok(inv, "", {
            "checkpoint_id": cid,
            "message_count": len(messages),
            "reason": reason,
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_session_export(inv: ToolInvocation) -> dict:
    """Export session messages to JSON or markdown."""
    ws = _caller_workspace(inv)
    sid = inv.arguments.get("session_id", "")
    fmt = str(inv.arguments.get("format", "markdown")).strip().lower()
    if not sid:
        return _error_inv(inv, "session_id is required")
    try:
        validate_workspace_id(ws)
        from storage.session_store import get_session
        s = get_session(sid, ws)
        if not s:
            return _error_inv(inv, "session not found")
        messages = s.get("messages", [])
        title = s.get("title", "session")
        if fmt == "json":
            export = {
                "session_id": sid,
                "title": title,
                "message_count": len(messages),
                "messages": messages,
            }
            return _ok(inv, "", {"format": "json", "export": export})
        else:
            lines = [f"# {title}", f"Session: {sid}", f"Messages: {len(messages)}", ""]
            for i, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                lines.append(f"## Message {i + 1} ({role})")
                lines.append("")
                lines.append(content[:1000])
                lines.append("")
            md = "\n".join(lines)
            return _ok(inv, "", {"format": "markdown", "export": md})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

__all__ = ['handle_session_get_summary', 'handle_session_create', 'handle_session_archive', 'handle_run_get_summary', 'handle_session_snapshot', 'handle_session_list_snapshots', 'handle_session_rewind', 'handle_session_checkpoint', 'handle_session_export']
