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

# {session_id: {"queue": queue.Queue, "last_access": float}}
_sessions: dict[str, dict] = {}
_lock = threading.Lock()
_MAX_IDLE_SEC = 600  # 10 min


def _cleanup():
    """Remove idle session queues."""
    now = time.time()
    stale = [sid for sid, s in _sessions.items() if now - s["last_access"] > _MAX_IDLE_SEC]
    for sid in stale:
        _sessions.pop(sid, None)


def push_event(session_id: str, event_type: str, data: dict):
    """Push an event to a session's SSE queue."""
    with _lock:
        _cleanup()
        if session_id not in _sessions:
            _sessions[session_id] = {"queue": queue.Queue(), "last_access": time.time()}
        else:
            _sessions[session_id]["last_access"] = time.time()
        q = _sessions[session_id]["queue"]
    try:
        q.put_nowait(json.dumps({"event": event_type, "data": data}, ensure_ascii=False))
    except queue.Full:
        pass


def push_tool_start(session_id: str, tool_id: str, step: int):
    push_event(session_id, "tool_call_started", {"tool_id": tool_id, "step": step})


def push_tool_done(session_id: str, tool_id: str, ok: bool, summary: str = ""):
    push_event(session_id, "tool_call_completed", {"tool_id": tool_id, "ok": ok, "summary": summary[:200]})


def push_token(session_id: str, text: str):
    push_event(session_id, "token", {"text": text})


def push_turn_done(session_id: str, turn_id: str, answer: str = ""):
    push_event(session_id, "turn_completed", {"turn_id": turn_id, "answer": answer[:500]})


def push_error(session_id: str, error_type: str, message: str):
    push_event(session_id, "error", {"type": error_type, "message": message[:200]})


def subscribe(session_id: str, timeout: int = 25) -> Optional[str]:
    """Block up to `timeout` seconds for the next SSE-formatted event line.

    Returns one SSE frame string, or None if timeout / no session.
    """
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = {"queue": queue.Queue(), "last_access": time.time()}
        _sessions[session_id]["last_access"] = time.time()
        q = _sessions[session_id]["queue"]
    try:
        raw = q.get(timeout=timeout)
        payload = json.loads(raw)
        return f"event: {payload['event']}\ndata: {json.dumps(payload['data'], ensure_ascii=False)}\n\n"
    except queue.Empty:
        return None
