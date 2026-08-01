from __future__ import annotations

import pytest

from workflows.service import WorkflowError, execute_workflow, save_workflow


def _definition():
    return {
        "workflow_id": "cross_extension",
        "name": "跨扩展文本流程",
        "nodes": [
            {
                "node_id": "first",
                "tool_id": "reference.insights.summarize",
                "arguments": {"text": "${input.text}"},
            },
            {
                "node_id": "second",
                "tool_id": "reference.insights.summarize",
                "depends_on": ["first"],
                "arguments": {"text": "上一步：${nodes.first.output.summary}"},
            },
        ],
    }


def test_cross_extension_dag_executes_and_persists_redacted_inputs(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from extensions.runtime import reset_extension_cache_for_tests
    from core.tools.integration import reset_default_client_for_tests
    reset_extension_cache_for_tests(); reset_default_client_for_tests()
    saved = save_workflow("default", _definition())
    assert saved["execution_order"] == ["first", "second"]
    run = execute_workflow("default", "cross_extension", {"text": "alpha beta", "api_token": "must-not-persist"})
    assert run["status"] == "succeeded"
    assert [item["status"] for item in run["nodes"]] == ["succeeded", "succeeded"]
    assert run["nodes"][1]["output"]["summary"] == "上一步：alpha beta"
    assert run["inputs"]["api_token"] == "[REDACTED_SECRET]"


def test_workflow_validation_rejects_cycles_and_unknown_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    cyclic = _definition()
    cyclic["nodes"][0]["depends_on"] = ["second"]
    with pytest.raises(WorkflowError, match="cycle"):
        save_workflow("default", cyclic)
    unknown = _definition()
    unknown["nodes"][0]["tool_id"] = "missing.tool"
    with pytest.raises(WorkflowError, match="unknown or disabled"):
        save_workflow("default", unknown)
    secret = _definition()
    secret["nodes"][0]["arguments"]["api_token"] = "literal-secret"
    with pytest.raises(WorkflowError, match="static secret"):
        save_workflow("default", secret)


def test_missing_runtime_input_is_recorded_as_a_failed_node(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from extensions.runtime import reset_extension_cache_for_tests
    from core.tools.integration import reset_default_client_for_tests
    reset_extension_cache_for_tests(); reset_default_client_for_tests()
    save_workflow("default", _definition())
    run = execute_workflow("default", "cross_extension", {})
    assert run["status"] == "failed"
    assert run["nodes"][0]["status"] == "failed"
    assert "not found" in run["nodes"][0]["errors"][0]


def test_workflow_job_runs_through_durable_job_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from extensions.runtime import reset_extension_cache_for_tests
    from core.tools.integration import reset_default_client_for_tests
    reset_extension_cache_for_tests(); reset_default_client_for_tests()
    save_workflow("default", _definition())
    from jobs.manager import create_job
    from jobs.runner import run_job
    from jobs.store import get_job
    job = create_job("default", "workflow_run", "Workflow", {"workflow_id": "cross_extension", "inputs": {"text": "queued text"}}, enqueue=False)
    run_job("default", job.job_id)
    completed = get_job("default", job.job_id)
    assert completed and completed.status == "succeeded"
    assert completed.result_summary["workflow_id"] == "cross_extension"
    assert completed.result_summary["workflow_run_id"].startswith("wfrun_")


def test_organization_workspace_isolation_and_workflow_roles(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("AGENT_PLATFORM_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("AGENT_PLATFORM_SESSION_SECRET", "workflow-session-secret")
    monkeypatch.setenv("AGENT_PLATFORM_MASTER_KEY", "workflow-master-key")
    monkeypatch.delenv("AGENT_PLATFORM_LOGIN_USERNAME", raising=False)
    monkeypatch.delenv("AGENT_PLATFORM_LOGIN_PASSWORD", raising=False)
    from backend.core.identity import upsert_user
    from storage.workspace_store import ensure_workspace
    ensure_workspace("team_a"); ensure_workspace("team_b"); ensure_workspace("root_owner")
    upsert_user("developer_a", "password", "developer", "org_a", ["team_a"])
    upsert_user("viewer_a", "password", "viewer", "org_a", ["team_a"])
    upsert_user("admin_b", "password", "admin", "org_b", ["team_b"])
    upsert_user("platform_owner", "password", "owner", "org_root", ["root_owner"])
    from extensions.runtime import reset_extension_cache_for_tests
    from core.tools.integration import reset_default_client_for_tests
    reset_extension_cache_for_tests(); reset_default_client_for_tests()
    from backend.main import create_app
    app = create_app(); app.config.update(TESTING=True)
    origin = {"Origin": "http://localhost:5273"}

    developer = app.test_client()
    developer.post("/api/auth/login", json={"username": "developer_a", "password": "password"}, headers=origin)
    created = developer.post("/api/workflows", json={**_definition(), "workspace_id": "team_a"}, headers=origin)
    assert created.status_code == 201
    assert developer.get("/api/workflows?workspace_id=team_b", headers=origin).status_code == 403

    viewer = app.test_client()
    viewer.post("/api/auth/login", json={"username": "viewer_a", "password": "password"}, headers=origin)
    denied = viewer.post("/api/workflows/cross_extension/runs", json={"workspace_id": "team_a", "inputs": {"text": "x"}}, headers=origin)
    assert denied.status_code == 403

    admin_b = app.test_client()
    admin_b.post("/api/auth/login", json={"username": "admin_b", "password": "password"}, headers=origin)
    assert admin_b.get("/api/workflows?workspace_id=team_a", headers=origin).status_code == 403
    assert admin_b.post("/api/identity/organizations", json={"organization_id": "forbidden_org", "name": "Forbidden"}, headers=origin).status_code == 403
    assert admin_b.post("/api/identity/users", json={"username": "escalated", "password": "password", "role": "owner", "organization_id": "org_b", "workspace_ids": ["team_b"]}, headers=origin).status_code == 403
    assert admin_b.post("/api/identity/users", json={"username": "developer_a", "password": "replaced", "role": "viewer", "organization_id": "org_b", "workspace_ids": ["team_b"]}, headers=origin).status_code == 403
    assert admin_b.post("/api/identity/organizations/org_b/memberships", json={"username": "developer_a", "role": "viewer", "workspace_ids": ["team_b"]}, headers=origin).status_code == 403

    original = app.test_client()
    assert original.post("/api/auth/login", json={"username": "developer_a", "password": "password"}, headers=origin).status_code == 200

    owner = app.test_client()
    owner.post("/api/auth/login", json={"username": "platform_owner", "password": "password"}, headers=origin)
    created_org = owner.post("/api/identity/organizations", json={"organization_id": "org_c", "name": "组织 C"}, headers=origin)
    assert created_org.status_code == 201
    assert owner.get("/api/identity/organizations", headers=origin).get_json()["organizations"]
    from backend.core.identity import assign_workspace
    with pytest.raises(ValueError, match="another organization"):
        assign_workspace("org_c", "team_a")
