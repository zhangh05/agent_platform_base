# agent/app/session_manager.py
"""Thread-safe AgentSession lifecycle boundary."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable

from agent.core.session import AgentSession

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(
        self,
        *,
        ttl_seconds: int = 3600,
        max_sessions: int = 1000,
        restore_callback: Callable[[AgentSession, str, str], None] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.restore_callback = restore_callback
        self._sessions: dict[str, AgentSession] = {}
        self._sessions_lock = threading.RLock()
        self._session_turn_locks: dict[str, threading.RLock] = {}

    def new_session_id(self) -> str:
        return f"session_{uuid.uuid4().hex[:12]}"

    def get_or_create(self, session_id: str | None, workspace_id: str):
        with self._sessions_lock:
            self._evict_expired_locked()
            sid = session_id or self.new_session_id()
            if sid not in self._sessions:
                session = AgentSession(session_id=sid, workspace_id=workspace_id)
                self._sessions[sid] = session
                self._session_turn_locks[sid] = threading.RLock()
                self._restore(session, sid, workspace_id)
            else:
                session = self._sessions[sid]
                # v3.1: Cross-workspace guard — a session from workspace A
                # must not be reused in workspace B.  If the workspace_id
                # does not match, create a new session with a fresh id.
                existing_ws = getattr(session, "workspace_id", "")
                if existing_ws and existing_ws != workspace_id:
                    new_sid = self.new_session_id()
                    session = AgentSession(session_id=new_sid, workspace_id=workspace_id)
                    self._sessions[new_sid] = session
                    self._session_turn_locks[new_sid] = threading.RLock()
                    self._restore(session, new_sid, workspace_id)
                    sid = new_sid
                else:
                    self._session_turn_locks.setdefault(sid, threading.RLock())
            session._last_access = time.time()
            return sid, session, self._session_turn_locks[sid]

    def snapshot(self) -> dict[str, Any]:
        with self._sessions_lock:
            return {
                "session_count": len(self._sessions),
                "ttl_seconds": self.ttl_seconds,
                "max_sessions": self.max_sessions,
                "sessions": [
                    {
                        "session_id": sid,
                        "workspace_id": getattr(sess, "workspace_id", ""),
                        "last_access": getattr(sess, "_last_access", 0),
                        "history_len": len(getattr(sess, "history", []) or []),
                    }
                    for sid, sess in self._sessions.items()
                ],
            }

    def _restore(self, session: AgentSession, sid: str, workspace_id: str) -> None:
        if not self.restore_callback:
            return
        try:
            self.restore_callback(session, sid, workspace_id)
        except Exception as exc:
            logger.warning("Failed to restore session %s: %s", sid, exc)

    def _evict_expired_locked(self) -> None:
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - getattr(s, "_last_access", 0) > self.ttl_seconds
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
            self._session_turn_locks.pop(sid, None)

        if len(self._sessions) <= self.max_sessions:
            return
        overflow = len(self._sessions) - self.max_sessions
        oldest = sorted(
            self._sessions.keys(),
            key=lambda sid: getattr(self._sessions[sid], "_last_access", 0),
        )
        for sid in oldest[:overflow]:
            self._sessions.pop(sid, None)
            self._session_turn_locks.pop(sid, None)
