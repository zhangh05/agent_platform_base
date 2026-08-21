from __future__ import annotations


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_LOGIN_ENABLED", "false")


def test_workflow_templates_are_listed_and_instantiated(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from backend.main import create_app

    client = create_app().test_client()
    listed = client.get("/api/workflow-templates")
    assert listed.status_code == 200
    templates = listed.get_json()["templates"]
    asset_template = next(template for template in templates if template["template_id"] == "network-asset-inventory")
    assert asset_template["input_example"] == {}
    assert "definition" not in asset_template

    created = client.post(
        "/api/workflow-templates/network-asset-inventory/instantiate",
        json={"workspace_id": "default"},
    )
    assert created.status_code == 201
    workflow = created.get_json()["workflow"]
    assert workflow["name"] == "网络资产清单核对"
    assert workflow["nodes"][0]["tool_id"] == "network.operations.assets_read"

    found = client.get("/api/workflows", query_string={"workspace_id": "default"})
    assert found.status_code == 200
    assert [item["workflow_id"] for item in found.get_json()["workflows"]] == [workflow["workflow_id"]]


def test_workflow_template_rejects_unknown_template(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from backend.main import create_app

    response = create_app().test_client().post(
        "/api/workflow-templates/no-such-template/instantiate",
        json={"workspace_id": "default"},
    )
    assert response.status_code == 404
    assert response.get_json()["error"] == "workflow_template_not_found"
