"""Atomic validation contract for task-centre bulk hard deletion."""


def _confirmation(*job_ids: str) -> str:
    return f"DELETE JOBS {','.join(sorted(job_ids))}"


def test_batch_delete_removes_selected_terminal_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_AUTH_ENABLED", "false")
    from backend.main import create_app
    from jobs.manager import create_job
    from jobs.store import get_job, update_job

    first = create_job("default", "agent_run", "first", {}, enqueue=False)
    second = create_job("default", "agent_run", "second", {}, enqueue=False)
    update_job("default", first.job_id, {"status": "succeeded"})
    update_job("default", second.job_id, {"status": "failed"})
    response = create_app().test_client().delete(
        "/api/jobs/batch-delete",
        json={"workspace_id": "default", "job_ids": [second.job_id, first.job_id], "confirmation": _confirmation(first.job_id, second.job_id)},
    )
    assert response.status_code == 200
    assert response.get_json()["job_ids"] == sorted([first.job_id, second.job_id])
    assert get_job("default", first.job_id) is None
    assert get_job("default", second.job_id) is None


def test_batch_delete_rejects_entire_selection_when_any_job_is_active(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_AUTH_ENABLED", "false")
    from backend.main import create_app
    from jobs.manager import create_job
    from jobs.store import get_job, update_job

    terminal = create_job("default", "agent_run", "terminal", {}, enqueue=False)
    active = create_job("default", "agent_run", "active", {}, enqueue=False)
    update_job("default", terminal.job_id, {"status": "succeeded"})
    update_job("default", active.job_id, {"status": "running"})
    response = create_app().test_client().delete(
        "/api/jobs/batch-delete",
        json={"workspace_id": "default", "job_ids": [terminal.job_id, active.job_id], "confirmation": _confirmation(terminal.job_id, active.job_id)},
    )
    assert response.status_code == 409
    assert get_job("default", terminal.job_id) is not None
    assert get_job("default", active.job_id) is not None
