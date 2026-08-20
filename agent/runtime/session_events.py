# agent/runtime/session_events.py
"""Per-session event bus for SSE streaming.

Runtime pushes execution events here; the HTTP SSE endpoint consumes them.
Each session gets its own queue. Old queues are cleaned up after 10 min idle.
"""

from __future__ import annotations

import queue
import threading
import time
import json
from typing import Optional

# {(principal-scoped workspace root, session_id): {"queue": ..., "last_access": ...}}
_sessions: dict[tuple[str, str], dict] = {}
_lock = threading.Lock()
_MAX_IDLE_SEC = 600  # 10 min
_MAX_QUEUE_SIZE = 256


def _cleanup():
    """Remove idle session queues."""
    now = time.time()
    stale = [key for key, s in _sessions.items() if now - s["last_access"] > _MAX_IDLE_SEC]
    for key in stale:
        _sessions.pop(key, None)


def _session_key(session_id: str, workspace_id: str) -> tuple[str, str]:
    """Bind transient stream events to the same principal/workspace as data."""
    if not workspace_id:
        raise ValueError("workspace_id is required for session events")
    from storage.paths import workspace_root
    return (str(workspace_root(workspace_id)), str(session_id))


def push_event(session_id: str, event_type: str, data: dict, *, workspace_id: str):
    """Push an event to a session's SSE queue."""
    with _lock:
        _cleanup()
        key = _session_key(session_id, workspace_id)
        if key not in _sessions:
            _sessions[key] = {"queue": queue.Queue(maxsize=_MAX_QUEUE_SIZE), "last_access": time.time()}
        else:
            _sessions[key]["last_access"] = time.time()
        q = _sessions[key]["queue"]
    try:
        q.put_nowait(json.dumps({"event": event_type, "data": data}, ensure_ascii=False))
    except queue.Full:
        # Preserve the newest state. SSE is an invalidation/observation path;
        # durable run and job records remain authoritative after a disconnect.
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(json.dumps({"event": event_type, "data": data}, ensure_ascii=False))
        except queue.Full:
            pass


def push_tool_start(session_id: str, tool_id: str, step: int, *, workspace_id: str):
    push_event(session_id, "tool_call_started", {"tool_id": tool_id, "step": step}, workspace_id=workspace_id)


def push_tool_done(session_id: str, tool_id: str, ok: bool, summary: str = "", *, workspace_id: str):
    push_event(session_id, "tool_call_completed", {"tool_id": tool_id, "ok": ok, "summary": summary[:200]}, workspace_id=workspace_id)


def push_token(session_id: str, text: str, *, workspace_id: str):
    push_event(session_id, "token", {"text": text}, workspace_id=workspace_id)


def push_turn_done(session_id: str, turn_id: str, answer: str = "", *, workspace_id: str):
    push_event(session_id, "turn_completed", {"turn_id": turn_id, "answer": answer[:500]}, workspace_id=workspace_id)


def push_error(session_id: str, error_type: str, message: str, *, workspace_id: str):
    push_event(session_id, "error", {"type": error_type, "message": message[:200]}, workspace_id=workspace_id)


def subscribe(session_id: str, timeout: int = 25, *, workspace_id: str) -> Optional[str]:
    """Block up to `timeout` seconds for the next SSE-formatted event line.

    Returns one SSE frame string, or None if timeout / no session.
    """
    with _lock:
        key = _session_key(session_id, workspace_id)
        if key not in _sessions:
            _sessions[key] = {"queue": queue.Queue(maxsize=_MAX_QUEUE_SIZE), "last_access": time.time()}
        _sessions[key]["last_access"] = time.time()
        q = _sessions[key]["queue"]
    try:
        raw = q.get(timeout=timeout)
        payload = json.loads(raw)
        return f"event: {payload['event']}\ndata: {json.dumps(payload['data'], ensure_ascii=False)}\n\n"
    except queue.Empty:
        return None
