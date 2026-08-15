from __future__ import annotations

import threading
import time

from backend.ws import agent_ws


class _SlowSocket:
    def __init__(self):
        self.sent = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def send(self, payload):
        self.entered.set()
        self.release.wait(timeout=3)
        self.sent.append(payload)


def test_broadcast_enqueue_does_not_block_runtime_thread(monkeypatch):
    slow = _SlowSocket()
    with agent_ws._active_ws_lock:
        agent_ws._active_ws_connections.clear()
        agent_ws._active_ws_connections["slow"] = ("alice", "ws-1", slow)

    started = time.monotonic()
    with monkeypatch.context() as ctx:
        ctx.setattr(agent_ws, "current_storage_principal", lambda: "alice", raising=False)
        # broadcast_ws_event imports this function lazily.
        import storage.principal
        ctx.setattr(storage.principal, "current_storage_principal", lambda: "alice")
        agent_ws.broadcast_ws_event({
            "name": "job_updated",
            "data": {"workspace_id": "ws-1", "job_id": "job-1"},
        })
    assert time.monotonic() - started < 0.1
    assert slow.entered.wait(timeout=1)
    slow.release.set()


def test_pending_broadcasts_coalesce_to_latest_snapshot():
    with agent_ws._broadcast_cv:
        agent_ws._broadcast_pending.clear()
        agent_ws._broadcast_pending["alice:ws-1:job-1"] = ("alice", "ws-1", "old")
        agent_ws._broadcast_pending["alice:ws-1:job-1"] = ("alice", "ws-1", "new")
        assert len(agent_ws._broadcast_pending) == 1
        assert next(iter(agent_ws._broadcast_pending.values()))[2] == "new"
