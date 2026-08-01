from __future__ import annotations

import json
import sys

import pytest

from agent.llm.router import resolve_model_route, resolve_model_candidates
from backend.core.identity import list_users, upsert_user, verify_user
from core.tools.mcp_client import McpProtocolError, McpServerConfig, StdioMcpClient
from evaluation.runner import GoldenCase, evaluate_case
from extensions.manifest import ExtensionManifest, ExtensionValidationError
from storage.object_store import LocalObjectStore


def test_extension_manifest_requires_declared_surface():
    manifest = ExtensionManifest("network.ops", "Network Ops", "1.0.0", capabilities=("network_inspection",))
    assert manifest.validate().extension_id == "network.ops"
    try:
        ExtensionManifest("empty", "Empty", "1.0.0").validate()
    except ExtensionValidationError:
        pass
    else:
        raise AssertionError("empty extension should be rejected")


def test_platform_evaluation_baseline():
    case = GoldenCase("read-file", "read a file", required_tools=("workspace.file",), required_terms=("完成",))
    result = evaluate_case(case, {"tool_ids": ["workspace.file"], "final_response": "任务已完成"})
    assert result["passed"] is True


def test_local_object_store_is_atomic_and_scoped(tmp_path):
    store = LocalObjectStore(tmp_path)
    assert store.put("runs/a.bin", b"abc") == "local://runs/a.bin"
    assert store.get("runs/a.bin") == b"abc"
    assert store.content_hash("runs/a.bin")
    try:
        store.get("../outside")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal should be rejected")


def test_identity_uses_hashed_password_and_role(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_PLATFORM_WORKSPACE_DIR", str(tmp_path))
    user = upsert_user("alice", "correct horse", "admin")
    assert user == {"username": "alice", "role": "admin", "organization_id": "default", "workspace_ids": ["default"]}
    assert verify_user("alice", "correct horse")["role"] == "admin"
    assert verify_user("alice", "wrong") is None
    assert "password_hash" not in json.dumps(list_users())


def test_model_route_preserves_active_provider_without_policy(monkeypatch):
    monkeypatch.delenv("AGENT_PLATFORM_MODEL_ROUTE_ASSISTANT_CHAT", raising=False)
    active = {"provider": "mock", "model": "mock-safe"}
    routed = resolve_model_route("assistant_chat", active)
    assert routed["provider"] == "mock"
    assert routed["routing"]["selected_by"] == "active_provider"


def test_identity_viewer_is_workspace_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_PLATFORM_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("AGENT_PLATFORM_SESSION_SECRET", "test-secret")
    monkeypatch.delenv("AGENT_PLATFORM_LOGIN_USERNAME", raising=False)
    monkeypatch.delenv("AGENT_PLATFORM_LOGIN_PASSWORD", raising=False)
    from storage.workspace_store import ensure_workspace
    ensure_workspace("tenant_a")
    ensure_workspace("tenant_b")
    upsert_user("scoped", "password", "viewer", "tenant_a", ["tenant_a"])
    from backend.main import create_app
    client = create_app().test_client()
    headers = {"Origin": "http://localhost:8011"}
    assert client.post("/api/auth/login", json={"username": "scoped", "password": "password"}, headers=headers).status_code == 200
    listed = client.get("/api/workspaces", headers=headers).get_json()["workspaces"]
    assert [item["workspace_id"] for item in listed] == ["tenant_a"]
    assert client.get("/api/workspaces/tenant_b/state", headers=headers).status_code == 403
    assert client.post("/api/workspaces", json={"workspace_id": "blocked"}, headers=headers).status_code == 403
    assert client.post("/api/workspaces/batch-delete", json={"workspace_ids": ["tenant_a"], "confirm": True}, headers=headers).status_code == 403

    upsert_user("scoped", "new-password", "operator", "tenant_b", ["tenant_b"])
    assert client.get("/api/workspaces/tenant_b/state", headers=headers).status_code == 200
    assert client.get("/api/workspaces/tenant_a/state", headers=headers).status_code == 403

    monkeypatch.setenv("AGENT_PLATFORM_API_TOKEN", "service-token")
    service_headers = {**headers, "X-API-Key": "service-token"}
    all_workspaces = client.get("/api/workspaces", headers=service_headers).get_json()["workspaces"]
    assert {item["workspace_id"] for item in all_workspaces} >= {"tenant_a", "tenant_b"}
    assert client.post("/api/workspaces", json={"workspace_id": "service_created"}, headers=service_headers).status_code == 200


def test_model_candidates_include_active_fallback(monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_MODEL_ROUTE_ASSISTANT_CHAT", "deepseek")
    monkeypatch.setattr("agent.llm.router.resolve_provider_llm_config", lambda provider: {"provider": provider, "model": "routed"})
    candidates = resolve_model_candidates("assistant_chat", {"provider": "openai", "model": "active"})
    assert [item["provider"] for item in candidates] == ["deepseek", "openai"]


def test_mcp_runs_through_governed_skill_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    from storage.workspace_store import ensure_workspace
    ensure_workspace("mcp_ws")
    server = """import json,sys
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); rid=m.get('id')
 if rid is None: continue
 if method=='initialize': result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'test','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'echo','description':'Echo','inputSchema':{'type':'object'}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':str(m.get('params',{}).get('arguments',{}).get('value',''))}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':rid,'result':result}),flush=True)
"""
    from core.tools.ecosystem import EcoRegistry, ExternalProvider
    provider = ExternalProvider(provider_type="mcp", name="test", status="enabled", trust_level="local", command=[sys.executable, "-c", server], tools=[{"tool_id": "echo", "enabled": True, "permissions": ["read"]}], permissions=["read"])
    EcoRegistry().save_provider("mcp_ws", provider)
    from core.tools.context import ToolRuntimeContext
    from core.tools.integration import reset_default_client_for_tests, get_default_tool_runtime_client
    reset_default_client_for_tests()
    client = get_default_tool_runtime_client()
    context = ToolRuntimeContext(workspace_id="mcp_ws", requested_by="turn_runner")
    listed = client.invoke("skill.manage", {"action": "mcp_list_tools", "provider_id": provider.provider_id}, context=context)
    called = client.invoke("skill.manage", {"action": "mcp_call", "provider_id": provider.provider_id, "tool_name": "echo", "arguments": {"value": "ok"}}, context=context)
    assert listed.status == "succeeded"
    assert called.status == "succeeded"
    assert "ok" in json.dumps(called.output)


def test_mcp_request_has_a_hard_timeout():
    server = "import sys,time; sys.stdin.readline(); time.sleep(10)"
    config = McpServerConfig("slow", (sys.executable, "-c", server), timeout_seconds=0.1)
    with pytest.raises(McpProtocolError, match="timed out"):
        with StdioMcpClient(config):
            pass


def test_provider_api_key_is_encrypted_at_rest(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("AGENT_PLATFORM_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("AGENT_PLATFORM_MASTER_KEY", "test-master-key-at-least-16")
    import agent.llm.provider_store as store
    providers_dir = tmp_path / "providers"
    monkeypatch.setattr(store, "PROVIDERS_DIR", providers_dir)

    saved = store.save_provider_config("openai", {"api_key": "sk-test-secret-value"})
    persisted = (providers_dir / "openai.json").read_text(encoding="utf-8")
    assert saved["api_key"] == "sk-test-secret-value"
    assert "sk-test-secret-value" not in persisted
    assert "secret://llm/openai" in persisted
    assert store.load_provider_config("openai")["api_key"] == "sk-test-secret-value"
    store.save_provider_config("openai", {"clear_api_key": True})
    assert store.load_provider_config("openai")["api_key"] == ""
    assert "secret_ref" not in (providers_dir / "openai.json").read_text(encoding="utf-8")
