"""Network inspections use only temporary internal worker Jobs."""


def test_running_network_inspection_is_hidden_from_task_center(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_AUTH_ENABLED", "false")

    from backend.main import create_app
    from jobs.manager import create_job

    inspection = create_job(
        "default", "network_inspection", "网络巡检 · 内部步骤", {}, enqueue=False,
        metadata={"task_center_visible": False, "job_role": "internal_inspection"},
    )
    visible = create_job("default", "agent_run", "用户任务", {}, enqueue=False)
    client = create_app().test_client()

    listed = client.get("/api/jobs?workspace_id=default").get_json()["jobs"]
    assert [item["job_id"] for item in listed] == [visible.job_id]

    # The temporary record remains addressable while a worker may still need
    # it for execution or cancellation.
    detail = client.get(f"/api/jobs/{inspection.job_id}?workspace_id=default")
    assert detail.status_code == 200
    assert detail.get_json()["job"]["job_id"] == inspection.job_id


def test_legacy_network_inspection_is_hidden_without_new_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_AUTH_ENABLED", "false")

    from backend.main import create_app
    from jobs.manager import create_job

    create_job("default", "network_inspection", "旧网络巡检", {}, enqueue=False)
    client = create_app().test_client()
    assert client.get("/api/jobs?workspace_id=default").get_json()["jobs"] == []


def test_reconcile_hard_deletes_terminal_legacy_inspection_job(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_AUTH_ENABLED", "false")

    from extensions.network_operations import service
    from jobs.manager import create_job, mark_succeeded
    from jobs.store import get_job

    task = {"task_id": "inspection_legacy", "status": "succeeded", "job_id": ""}
    job = create_job(
        "default", "network_inspection", "旧网络巡检", {"task_id": task["task_id"]}, enqueue=False,
        metadata={"task_center_visible": False, "job_role": "internal_inspection"},
    )
    task["job_id"] = job.job_id
    service._store("default").save("inspections", task["task_id"], task)
    from jobs.manager import mark_running
    mark_running("default", job.job_id)
    mark_succeeded("default", job.job_id)

    service.reconcile_network_state()

    assert get_job("default", job.job_id) is None
    retained = service.get_inspection("default", task["task_id"])
    assert retained["status"] == "succeeded"
    assert "job_id" not in retained
