import queue
import uuid

import pytest


def test_ws_heartbeat_payload_reports_monotonic_elapsed_time():
    from backend.ws.agent_ws import _heartbeat_payload

    payload = _heartbeat_payload(10.0, 12.75)

    assert payload == {
        "type": "event",
        "name": "heartbeat",
        "data": {"type": "heartbeat", "elapsed_ms": 2750},
    }


def test_resumable_stream_replays_only_frames_after_cursor():
    from backend.ws.agent_ws import _ResumableTurnStream

    stream = _ResumableTurnStream(str(uuid.uuid4()), "Admin", "default", "s-1")
    stream.put({"type": "event", "name": "planner_started", "data": {}})
    stream.put({"type": "token", "content": "hello"})
    stream.put({"type": "done", "final_response": "hello"})

    replay, terminal, latest = stream.events_after(1, timeout=0)

    assert [frame["type"] for frame in replay] == ["token", "done"]
    assert [frame["stream_seq"] for frame in replay] == [2, 3]
    assert terminal is True
    assert latest == 3


def test_transport_disconnect_does_not_cancel_resumable_turn():
    from backend.ws.agent_ws import _ResumableTurnStream, _stream_turn_to_socket

    class DisconnectedSocket:
        def send(self, _payload):
            raise ConnectionError("browser refreshed")

    stream = _ResumableTurnStream(str(uuid.uuid4()), "Admin", "default", "s-1")
    stream.put({"type": "event", "name": "planner_started", "data": {}})

    with pytest.raises(ConnectionError):
        _stream_turn_to_socket(DisconnectedSocket(), stream)

    assert stream.cancel_event.is_set() is False


def test_resumable_stream_lookup_is_scoped_to_owner_and_workspace():
    from backend.ws import agent_ws

    stream_id = str(uuid.uuid4())
    stream = agent_ws._ResumableTurnStream(stream_id, "Admin", "default", "s-1")
    assert agent_ws._register_resumable_turn(stream) is True
    try:
        assert agent_ws._lookup_resumable_turn(stream_id, "Admin", "default") is stream
        assert agent_ws._lookup_resumable_turn(stream_id, "other", "default") is None
        assert agent_ws._lookup_resumable_turn(stream_id, "Admin", "other") is None
        assert agent_ws._register_resumable_turn(stream) is False
    finally:
        with agent_ws._resumable_turns_lock:
            agent_ws._resumable_turns.pop(stream_id, None)


def test_ws_done_payload_includes_full_inspector_fields(monkeypatch):
    from backend.ws import agent_ws
    import agent.app.service as service

    class FakeResult:
        def to_dict(self):
            return {
                "ok": True,
                "final_response": "answer",
                "session_id": "s-1",
                "turn_id": "t-1",
                "trace_id": "trace-1",
                "events": [
                    {"event_id": "ev-1", "type": "tool_call", "timestamp": 1.0},
                    {"event_id": "ev-2", "type": "final", "timestamp": 2.0},
                ],
                "tool_calls": [
                    {"call_id": "call-1", "tool_id": "knowledge.manage", "ok": True},
                ],
                "metadata": {"source_count": 1},
                "warnings": [],
                "errors": [],
                "tool_decision": {"needed": True, "selected_tools": ["knowledge.manage"]},
                "no_tool_reason": "",
            }

    class FakeApp:
        def submit_user_message(self, **_kwargs):
            return FakeResult()

    monkeypatch.setattr(service, "get_default_agent_app", lambda: FakeApp())

    event_queue = queue.Queue()
    error_holder = {"error": None}
    stats = {"live_events": 0}
    agent_ws._run_agent_thread("q", "s-1", "default", {}, event_queue, error_holder, stats)

    messages = []
    while not event_queue.empty():
        messages.append(event_queue.get())
    done = next(item for item in messages if isinstance(item, dict) and item.get("type") == "done")

    assert done["trace_id"] == "trace-1"
    assert len(done["events"]) == 2
    assert done["tool_decision"]["selected_tools"] == ["knowledge.manage"]
    assert done["tool_calls"][0]["tool_id"] == "knowledge.manage"
    assert done["metadata"]["stream_mode"] == "event_replay_fallback"
    assert done["metadata"]["transport"] == "websocket"
    assert error_holder["error"] is None


def test_ws_worker_injects_cooperative_cancel_check(monkeypatch):
    import threading
    from backend.ws import agent_ws
    import agent.app.service as service

    captured = {}

    class FakeResult:
        def to_dict(self):
            return {"ok": True, "final_response": "done", "events": [], "tool_calls": [], "metadata": {}}

    class FakeApp:
        def submit_user_message(self, **kwargs):
            captured.update(kwargs)
            return FakeResult()

    monkeypatch.setattr(service, "get_default_agent_app", lambda: FakeApp())
    cancel_event = threading.Event()
    event_queue = queue.Queue()
    agent_ws._run_agent_thread(
        "q", "s-1", "default", {}, event_queue,
        {"error": None}, {"live_events": 0}, cancel_event,
    )
    check = captured["metadata"]["cancel_check"]
    assert check() is False
    cancel_event.set()
    assert check() is True


def test_ws_live_tool_summary_is_bounded_without_truncating_done_payload(monkeypatch):
    from backend.ws import agent_ws
    import agent.app.service as service
    from agent.runtime.stream_emitter import StreamEmitter

    long_summary = "x" * 20_000

    class FakeResult:
        def to_dict(self):
            return {
                "ok": True,
                "final_response": long_summary,
                "events": [],
                "tool_calls": [],
                "metadata": {},
            }

    class FakeApp:
        def submit_user_message(self, **_kwargs):
            StreamEmitter().emit("tool_result", {
                "tool_id": "knowledge.manage",
                "ok": True,
                "summary": long_summary,
            })
            return FakeResult()

    monkeypatch.setattr(service, "get_default_agent_app", lambda: FakeApp())
    event_queue = queue.Queue()
    agent_ws._run_agent_thread(
        "q", "s-1", "default", {}, event_queue,
        {"error": None}, {"live_events": 0},
    )
    messages = []
    while not event_queue.empty():
        messages.append(event_queue.get())
    live = next(item for item in messages if item.get("type") == "event")
    done = next(item for item in messages if item.get("type") == "done")
    assert len(live["data"]["summary"]) == 8003
    assert done["final_response"] == long_summary
