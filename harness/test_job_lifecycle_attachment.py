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
