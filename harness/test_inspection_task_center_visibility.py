"""Network inspections are internal workbench activity, not task-centre jobs."""


def test_network_inspection_is_hidden_from_task_center_but_remains_addressable(monkeypatch, tmp_path):
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

    # The durable record is still available to the network extension's worker
    # and lifecycle APIs; hiding it must not delete or orphan it.
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
