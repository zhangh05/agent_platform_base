"""Regression tests for run-to-job lifecycle attachment."""

from __future__ import annotations


def test_find_existing_session_job_does_not_require_create_locals(monkeypatch):
    import jobs.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "list_jobs", lambda **_kwargs: [{
        "job_id": "job_existing",
        "payload": {"session_id": "session_1"},
    }])
    monkeypatch.setattr(
        lifecycle,
        "create_job",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not create")),
    )

    assert lifecycle._find_or_create_job("default", "session_1", "hello") == "job_existing"


def test_create_session_job_returns_and_broadcasts_new_id(monkeypatch):
    import jobs.lifecycle as lifecycle

    broadcasts = []
    monkeypatch.setattr(lifecycle, "list_jobs", lambda **_kwargs: [])
    monkeypatch.setattr(
        lifecycle,
        "create_job",
        lambda **kwargs: {"job_id": "job_new", "title": kwargs["title"]},
    )
    monkeypatch.setattr(
        lifecycle,
        "_broadcast_job",
        lambda job_id, ws_id, session_id="": broadcasts.append((job_id, ws_id, session_id)),
    )

    assert lifecycle._find_or_create_job("default", "session_2", "new request") == "job_new"
    assert broadcasts == [("job_new", "default", "session_2")]


def test_failed_session_job_reactivates_for_new_user_turn(monkeypatch):
    import jobs.lifecycle as lifecycle

    rec = type("Rec", (), {"status": "failed"})()
    calls = []
    monkeypatch.setattr(lifecycle, "get_job", lambda *_args: rec)
    monkeypatch.setattr(lifecycle, "mark_running", lambda ws, job: calls.append((ws, job)))
    monkeypatch.setattr(lifecycle, "_broadcast_job", lambda *_args, **_kwargs: None)

    lifecycle._ensure_running("default", "job_failed")

    assert calls == [("default", "job_failed")]


def test_failed_to_running_is_valid_session_transition():
    from jobs.manager import _check_transition

    assert _check_transition("failed", "running") is True
