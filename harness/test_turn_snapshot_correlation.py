"""Regression coverage for stale terminal writes on reused session jobs."""

from types import SimpleNamespace


def test_stale_terminal_snapshot_cannot_close_new_active_turn(monkeypatch):
    import jobs.lifecycle as lifecycle

    record = SimpleNamespace(
        cancel_requested=False,
        metadata={
            "active_turn": {
                "client_request_id": "request-new",
                "status": "running",
                "stage": "turn_started",
                "run_id": "",
            },
        },
    )
    updates = []
    monkeypatch.setattr(lifecycle, "get_job", lambda *_args: record)
    monkeypatch.setattr(lifecycle, "update_job", lambda *_args: updates.append(_args[2]))
    monkeypatch.setattr(lifecycle, "_broadcast_job", lambda *_args: None)

    lifecycle.finish_session_turn_snapshot(
        "ws-1",
        "job-1",
        "session-1",
        client_request_id="request-old",
        run_id="run-old",
        ok=False,
        error="late old failure",
    )

    assert updates == []
