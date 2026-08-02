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


def test_successful_turn_is_closed_after_attachment(monkeypatch):
    import jobs.lifecycle as lifecycle

    calls = []
    monkeypatch.setattr(lifecycle, "_find_or_create_job", lambda *_args: "job_1")
    monkeypatch.setattr(lifecycle, "_ensure_running", lambda *_args: calls.append("running"))
    monkeypatch.setattr(lifecycle, "_merge_run_id", lambda *_args: calls.append("merged"))
    monkeypatch.setattr(
        lifecycle,
        "_finish_turn",
        lambda *_args, **kwargs: calls.append(("finished", kwargs["run_ok"])),
    )

    job_id = lifecycle.attach_run_to_session_job(
        "default", "session_1", "run_1", run_ok=True,
    )

    assert job_id == "job_1"
    assert calls == ["running", "merged", ("finished", True)]


def test_failed_turn_closes_job_as_failed(monkeypatch):
    import jobs.lifecycle as lifecycle

    calls = []
    monkeypatch.setattr(lifecycle, "mark_failed", lambda ws, job, error="": calls.append((ws, job, error)))
    monkeypatch.setattr(lifecycle, "_broadcast_job", lambda *_args, **_kwargs: None)

    lifecycle._finish_turn(
        "default", "job_1", "session_1", "run_1",
        run_ok=False, error="provider_failed",
    )

    assert calls == [("default", "job_1", "provider_failed")]


def test_successful_turn_closes_job_as_succeeded(monkeypatch):
    import jobs.lifecycle as lifecycle

    calls = []
    monkeypatch.setattr(
        lifecycle,
        "mark_succeeded",
        lambda ws, job, result_summary=None: calls.append((ws, job, result_summary)),
    )
    monkeypatch.setattr(lifecycle, "_broadcast_job", lambda *_args, **_kwargs: None)

    lifecycle._finish_turn(
        "default", "job_1", "session_1", "run_1", run_ok=True,
    )

    assert calls == [("default", "job_1", {"latest_run_id": "run_1"})]
