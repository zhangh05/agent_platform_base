from __future__ import annotations

import json

from agent.llm.router import resolve_model_route
from backend.core.identity import list_users, upsert_user, verify_user
from core.tools.mcp_client import McpServerConfig, StdioMcpClient
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
    assert user == {"username": "alice", "role": "admin", "organization_id": "default"}
    assert verify_user("alice", "correct horse")["role"] == "admin"
    assert verify_user("alice", "wrong") is None
    assert "password_hash" not in json.dumps(list_users())


def test_model_route_preserves_active_provider_without_policy(monkeypatch):
    monkeypatch.delenv("AGENT_PLATFORM_MODEL_ROUTE_ASSISTANT_CHAT", raising=False)
    active = {"provider": "mock", "model": "mock-safe"}
    routed = resolve_model_route("assistant_chat", active)
    assert routed["provider"] == "mock"
    assert routed["routing"]["selected_by"] == "active_provider"
