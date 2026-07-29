"""Real-time runtime event emission shared by HTTP and WebSocket entrypoints."""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional


def build_trace_id() -> str:
    return str(uuid.uuid4())


class StreamEvent:
    RUN_STARTED = "run_started"
    MODEL_STARTED = "model_started"
    TOOL_CALL = "tool_call"
    APPROVAL_REQUIRED = "approval_required"
    TOOL_RESULT = "tool_result"
    COMPACT = "compact"
    FINAL = "final"
    ERROR = "error"


class StreamEmitter:
    """Collect events and optionally publish them to the active request callback."""

    _tls = None
    _realtime_cv: contextvars.ContextVar[Optional[Callable]] = contextvars.ContextVar(
        "stream_realtime", default=None,
    )

    @classmethod
    def _tls_state(cls):
        if cls._tls is None:
            import threading
            cls._tls = threading.local()
        return cls._tls

    @classmethod
    def set_realtime_callback(cls, callback) -> None:
        cls._tls_state().realtime = callback
        cls._realtime_cv.set(callback)

    @classmethod
    def clear_realtime_callback(cls) -> None:
        if cls._tls is not None:
            cls._tls.realtime = None
        cls._realtime_cv.set(None)

    @classmethod
    def _get_realtime(cls):
        callback = cls._realtime_cv.get()
        if callback is not None:
            return callback
        if cls._tls is not None:
            return getattr(cls._tls, "realtime", None)
        return None

    def __init__(self):
        self._events: list[dict] = []

    def emit(self, event_type: str, data: dict) -> None:
        event = {
            "type": event_type,
            "timestamp": time.time(),
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        self._events.append(event)
        callback = self._get_realtime()
        if callback:
            try:
                callback(event)
            except Exception:
                logging.getLogger(__name__).warning(
                    "StreamEmitter callback failed", exc_info=True,
                )

    def to_events(self) -> list[dict]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()


__all__ = ["StreamEmitter", "StreamEvent", "build_trace_id"]
