# agent/audit/trace.py
"""TraceRecorder — captures model requests/responses and tool calls."""

import threading
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field


@dataclass
class TraceEntry:
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turn_id: str = ""
    step: int = 0
    type: str = ""  # model_request | model_response | tool_call | tool_result
    data: dict = field(default_factory=dict)
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TraceRecorder:
    def __init__(self):
        self._entries: list = []
        self._lock = threading.Lock()

    def record_model_request(self, turn_id: str, step: int, messages_summary: str, tools_count: int):
        with self._lock:
            self._entries.append(TraceEntry(
                turn_id=turn_id, step=step, type="model_request",
                data={"messages_summary": messages_summary, "tools_count": tools_count},
            ))

    def record_model_response(self, turn_id: str, step: int, has_content: bool, has_tool_calls: bool, finish_reason: str = ""):
        with self._lock:
            self._entries.append(TraceEntry(
                turn_id=turn_id, step=step, type="model_response",
                data={"has_content": has_content, "has_tool_calls": has_tool_calls, "finish_reason": finish_reason},
            ))

    def record_tool_call(self, turn_id: str, step: int, tool_id: str, args_summary: str):
        with self._lock:
            self._entries.append(TraceEntry(
                turn_id=turn_id, step=step, type="tool_call",
                data={"tool_id": tool_id, "args_summary": args_summary},
            ))

    def record_tool_result(self, turn_id: str, step: int, tool_id: str, ok: bool, summary: str):
        with self._lock:
            self._entries.append(TraceEntry(
                turn_id=turn_id, step=step, type="tool_result",
                data={"tool_id": tool_id, "ok": ok, "summary": summary[:500]},
            ))

    def entries_for_turn(self, turn_id: str) -> list:
        with self._lock:
            return [e for e in list(self._entries) if e.turn_id == turn_id]
