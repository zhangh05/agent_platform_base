from __future__ import annotations


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_LOGIN_ENABLED", "false")


def test_network_extension_no_longer_publishes_advanced_workflow_templates(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from backend.main import create_app

    client = create_app().test_client()
    listed = client.get("/api/workflow-templates")
    assert listed.status_code == 200
    templates = listed.get_json()["templates"]
    assert all(not item["template_id"].startswith("network-operations-") for item in templates)


def test_workflow_template_rejects_unknown_template(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from backend.main import create_app

    response = create_app().test_client().post(
        "/api/workflow-templates/no-such-template/instantiate",
        json={"workspace_id": "default"},
    )
    assert response.status_code == 404
    assert response.get_json()["error"] == "workflow_template_not_found"


def test_workflow_delete_requires_confirmation_and_removes_definition(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from backend.main import create_app
    client = create_app().test_client()
    created = client.post("/api/workflows", json={"workspace_id": "default", "workflow_id": "delete-test", "name": "删除测试", "nodes": [{"node_id": "read", "tool_id": "network.operations.devices_read", "arguments": {}}]})
    assert created.status_code == 201
    workflow_id = created.get_json()["workflow"]["workflow_id"]
    blocked = client.delete(f"/api/workflows/{workflow_id}", json={"workspace_id": "default"})
    assert blocked.status_code == 400
    assert blocked.get_json()["error"] == "workflow_delete_confirmation_required"
    deleted = client.delete(
        f"/api/workflows/{workflow_id}",
        json={"workspace_id": "default", "confirm": "delete"},
    )
    assert deleted.status_code == 200
    assert deleted.get_json()["deleted"] == {"workflow_id": workflow_id, "removed_runs": 0}
    assert client.get(f"/api/workflows/{workflow_id}", query_string={"workspace_id": "default"}).status_code == 404
    listed = client.get("/api/workflows", query_string={"workspace_id": "default"})
    assert listed.get_json()["workflows"] == []


def test_workflow_hard_delete_removes_all_runs_without_list_limit(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from workflows.service import _save_run, delete_workflow, save_workflow

    workflow = save_workflow("default", {
        "workflow_id": "hard_delete_all_runs",
        "name": "硬删除验证",
        "nodes": [{
            "node_id": "read",
            "tool_id": "network.operations.devices_read",
            "arguments": {},
        }],
    })
    for index in range(501):
        _save_run({
            "workspace_id": "default",
            "run_id": f"delete_run_{index}",
            "workflow_id": workflow["workflow_id"],
            "status": "succeeded",
            "nodes": [],
        })

    deleted = delete_workflow("default", workflow["workflow_id"])

    assert deleted == {"workflow_id": workflow["workflow_id"], "removed_runs": 501}
    run_files = tmp_path / "workspaces" / "default" / "workflows" / "runs"
    definition_files = tmp_path / "workspaces" / "default" / "workflows" / "definitions"
    assert not list(run_files.glob("*.json"))
    assert not list(definition_files.glob("*.json"))
