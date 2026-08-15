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


def test_ws_turn_rejects_realtime_callback_after_terminal(monkeypatch):
    import agent.app.service as service

    late_callbacks = []

    class FakeResult:
        def to_dict(self):
            return {
                "ok": True, "final_response": "done", "events": [], "tool_calls": [],
                "metadata": {}, "warnings": [], "errors": [], "tool_decision": {},
            }

    class FakeApp:
        def submit_user_message(self, **_kwargs):
            late_callbacks.append(StreamEmitter._get_realtime())
            return FakeResult()

    monkeypatch.setattr(service, "get_default_agent_app", lambda: FakeApp())
    frames: queue.Queue = queue.Queue()
    agent_ws._run_agent_thread(
        "q", "session-late", "workspace-test", {}, frames,
        {"error": None}, {"live_events": 0},
    )

    # This is the callback captured by an asyncio.to_thread provider task.
    # It may finish after the synchronous turn has already emitted done.
    late_callbacks[0]({"type": "token", "content": "late"})

    messages = []
    while not frames.empty():
        messages.append(frames.get())
    assert [frame["content"] for frame in messages if isinstance(frame, dict) and frame.get("type") == "token"] == []
    assert any(isinstance(frame, dict) and frame.get("type") == "done" for frame in messages)
    assert messages[-1] is None
