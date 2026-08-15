from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

from agent.runtime.stream_emitter import StreamEmitter
from backend.ws import agent_ws


class _FakeApp:
    def submit_user_message(self, **_kwargs):
        emitter = StreamEmitter()
        for token in ("A", "B", "C"):
            emitter.emit("token", {"content": token})
        return SimpleNamespace(to_dict=lambda: {
            "ok": True,
            "session_id": "session-backpressure",
            "turn_id": "turn-backpressure",
            "trace_id": "trace-backpressure",
            "final_response": "ABC",
            "events": [],
            "tool_calls": [],
            "warnings": [],
            "errors": [],
            "tool_decision": {},
            "no_tool_reason": "",
        })


def test_connected_slow_consumer_does_not_drop_stream_tokens(monkeypatch):
    monkeypatch.setattr(
        "agent.app.service.get_default_agent_app",
        lambda: _FakeApp(),
    )

    frames: queue.Queue = queue.Queue(maxsize=1)
    errors = {"error": None}
    stats: dict = {"live_events": 0}
    transport_closed = threading.Event()
    worker = threading.Thread(
        target=agent_ws._run_agent_thread,
        args=(
            "test", "session-backpressure", "workspace-test", {}, frames,
            errors, stats, threading.Event(), "", transport_closed,
        ),
        daemon=True,
    )
    worker.start()

    # Let the first token fill the bounded queue. The next token must wait for
    # capacity rather than being silently discarded after the old 200ms timeout.
    time.sleep(0.35)
    received = []
    while True:
        frame = frames.get(timeout=3)
        if frame is None:
            break
        received.append(frame)

    worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors["error"] is None
    assert [item["content"] for item in received if item["type"] == "token"] == ["A", "B", "C"]
    assert any(item["type"] == "done" for item in received)
