"""Terminal job hard-delete lifecycle."""


def test_terminal_job_delete_removes_record_events_and_logs(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_AUTH_ENABLED", "false")

    from backend.main import create_app
    app = create_app()
    app.config.update(TESTING=True)
    from jobs.manager import create_job
    from jobs.schemas import JobEvent
    from jobs.store import append_event, append_log, get_job, update_job

    record = create_job("default", "network_inspection", "巡检记录", {}, enqueue=False)
    update_job("default", record.job_id, {"status": "succeeded"})
    append_event("default", record.job_id, JobEvent(job_id=record.job_id, workspace_id="default", event_type="completed"))
    append_log("default", record.job_id, "done")

    response = app.test_client().delete(
        f"/api/jobs/{record.job_id}",
        json={"workspace_id": "default", "confirmation": f"DELETE {record.job_id}"},
    )

    assert response.status_code == 200
    assert response.get_json()["deleted"] is True
    assert get_job("default", record.job_id) is None


def test_running_job_cannot_be_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_AUTH_ENABLED", "false")

    from backend.main import create_app
    app = create_app()
    app.config.update(TESTING=True)
    from jobs.manager import create_job
    from jobs.store import get_job, update_job

    record = create_job("default", "network_inspection", "运行中巡检", {}, enqueue=False)
    update_job("default", record.job_id, {"status": "running"})

    response = app.test_client().delete(
        f"/api/jobs/{record.job_id}",
        json={"workspace_id": "default", "confirmation": f"DELETE {record.job_id}"},
    )

    assert response.status_code == 409
    assert get_job("default", record.job_id) is not None
