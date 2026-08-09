from __future__ import annotations

import json
from types import SimpleNamespace

from extensions.network_operations import service
from extensions.network_operations.backend import assets_read, assets_write, inspection
from extensions.network_operations.device_tools import (
    MAX_READ_ONLY_COMMANDS,
    is_read_only_command as device_is_read_only_command,
)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("AGENT_PLATFORM_MASTER_KEY", "test-extension-master-key")


def test_assets_store_only_an_encrypted_credential_reference(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = service.save_asset("default", {
        "name": "核心交换机", "host": "10.0.0.1", "port": 22,
        "username": "netops", "password": "sensitive-password", "vendor": "h3c",
    })
    assert asset["credential_configured"] is True
    assert "sensitive-password" not in json.dumps(service.list_assets("default"))
    persisted = (tmp_path / "workspaces" / "_runtime" / "secrets" / "encrypted.json").read_text()
    assert "sensitive-password" not in persisted


def test_private_key_assets_do_not_expose_plaintext(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = service.save_asset("default", {
        "name": "汇聚交换机", "host": "10.0.0.9", "port": 22,
        "username": "netops", "auth_method": "private_key",
        "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-key\n-----END OPENSSH PRIVATE KEY-----",
        "key_passphrase": "secret-passphrase", "vendor": "huawei",
    })
    assert asset["credential_configured"] is True
    assert asset["auth_method"] == "private_key"
    visible = json.dumps(service.list_assets("default"), ensure_ascii=False)
    assert "secret-key" not in visible
    assert "secret-passphrase" not in visible
    persisted = (tmp_path / "workspaces" / "_runtime" / "secrets" / "encrypted.json").read_text()
    assert "secret-key" not in persisted
    assert "secret-passphrase" not in persisted


def test_probe_requires_and_then_saves_host_key(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = service.save_asset("default", {
        "name": "核心交换机", "host": "10.0.0.1", "username": "netops",
        "password": "sensitive-password", "vendor": "h3c",
    })

    def fake_probe(_target, *, accept_host_key=False, read=False, **_kwargs):
        if not accept_host_key:
            return {"ok": False, "status": "blocked", "fingerprint": "SHA256:test", "requires_host_key_acceptance": True}
        return {"ok": True, "status": "succeeded", "fingerprint": "SHA256:test", "stages": [{"name": "auth", "status": "ok"}]}

    monkeypatch.setattr(service, "probe_target", fake_probe)
    blocked = service.probe_asset("default", asset["asset_id"])
    assert blocked["requires_host_key_acceptance"] is True
    accepted = service.probe_asset("default", asset["asset_id"], accept_host_key=True)
    assert accepted["ok"] is True
    assert accepted["host_key_saved"] is True
    assert service.get_asset("default", asset["asset_id"])["host_key_trusted"] is True


def test_inspection_baseline_and_diff_close_the_read_only_loop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = service.save_asset("default", {
        "name": "核心交换机", "host": "10.0.0.1", "username": "netops",
        "password": "sensitive-password", "vendor": "h3c",
    })
    first = service.start_inspection(
        "default", [asset["asset_id"]],
        collector=lambda _asset, commands: {command: "healthy" for command in commands},
        background=False,
    )
    assert first["status"] == "succeeded"
    assert first["artifact_id"]
    baseline = service.create_baseline("default", first["task_id"], confirm=True)
    assert baseline["current"] is True
    second = service.start_inspection(
        "default", [asset["asset_id"]],
        collector=lambda _asset, commands: {command: "changed" for command in commands},
        background=False,
    )
    diff = service.diff_against_current("default", second["task_id"])
    assert diff["changed"] is True
    assert diff["changes"][0]["asset_id"] == asset["asset_id"]


def test_write_commands_are_rejected():
    assert service.is_read_only_command("display version") is True
    assert service.is_read_only_command("show interfaces status") is True
    assert service.is_read_only_command("system-view") is False
    assert service.is_read_only_command("reload") is False
    assert service.is_read_only_command("display version; reboot") is False
    assert service.is_read_only_command is device_is_read_only_command


def test_read_only_command_limit_is_shared_and_enforced():
    commands = ["display version"] * (MAX_READ_ONLY_COMMANDS + 1)
    try:
        service.commands_for({"vendor": "h3c"}, commands)
    except ValueError as exc:
        assert "1 to 20" in str(exc)
    else:
        raise AssertionError("command count above the shared limit must fail")


def test_assets_write_requires_explicit_action_and_non_empty_asset(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    invocation = SimpleNamespace(workspace_id="default", arguments={"action": "save", "asset": {}})
    assert assets_write(invocation) == {
        "ok": False,
        "error": "non-empty asset object is required for save",
    }

    invalid = SimpleNamespace(workspace_id="default", arguments={"action": "list", "asset": {"name": "ignored"}})
    assert assets_write(invalid)["error"] == "unsupported action; expected save or delete"

    saved = assets_write(SimpleNamespace(workspace_id="default", arguments={
        "action": "save",
        "asset": {"name": "R1", "host": "10.0.0.8", "username": "ops", "password": "secret"},
    }))
    assert saved["ok"] is True
    assert "action" not in service.get_asset("default", saved["asset"]["asset_id"], include_secret=True)


def test_network_reads_report_missing_records_as_failures(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    missing_asset = assets_read(SimpleNamespace(
        workspace_id="default", arguments={"asset_id": "asset_missing"},
    ))
    missing_task = inspection(SimpleNamespace(
        workspace_id="default", arguments={"action": "get", "task_id": "inspection_missing"},
    ))
    assert missing_asset == {"ok": False, "error": "asset_not_found", "asset_id": "asset_missing"}
    assert missing_task == {"ok": False, "error": "inspection_not_found", "task_id": "inspection_missing"}


def test_device_manage_is_registered_as_network_operations_extension_tool():
    from extensions.runtime import get_extension_tool_specs

    specs = {spec.tool_id: spec for spec, _handler in get_extension_tool_specs()}
    assert "network.operations.device.manage" in specs
    spec = specs["network.operations.device.manage"]
    assert spec.category == "ops"
    assert spec.risk_level == "medium"
    assert spec.permission_action == "network"
    properties = set((spec.input_schema or {}).get("properties") or {})
    for required in {"action", "asset_id", "host", "commands", "accept_host_key"}:
        assert required in properties, required


def test_extension_tools_are_registered_with_runtime_risk_contracts():
    from core.runtime_engine.contracts import get_contract

    contract = get_contract("network.operations.device.manage")
    assert contract is not None
    assert contract.risk_level == "medium"
    assert contract.side_effect == "external_request"


def test_read_only_extension_tools_get_safe_retry_contracts():
    from core.runtime_engine.contracts import get_retry_contract

    contract = get_retry_contract("network.operations.assets_read", {})
    assert contract is not None
    assert contract.idempotent is True
    assert contract.max_retries >= 1


def test_device_manage_is_exposed_to_llm_as_extension_tool():
    from core.tools.canonical_registry import CANONICAL_REGISTRY, to_openai_tools, to_tool_specs

    assert "device.manage" not in CANONICAL_REGISTRY
    tool_specs = {spec.tool_id: spec for spec, _handler in to_tool_specs()}
    assert "network.operations.device.manage" in tool_specs
    openai_names = {
        tool["function"]["name"]
        for tool in to_openai_tools()
        if tool.get("type") == "function"
    }
    assert "device__manage" not in openai_names
    assert "network__operations__device__manage" in openai_names


def test_network_extension_llm_descriptions_expose_actions_and_arguments():
    from agent.runtime.ssot_runtime import _build_ssot_runtime_tool_registry
    from core.runtime_engine.query_loop import _build_cached_tool_definitions

    registry = _build_ssot_runtime_tool_registry([
        "network.operations.device.manage",
        "network.operations.inspection",
        "network.operations.baseline",
    ])
    descriptions = {
        tool["function"]["name"]: tool["function"]["description"]
        for tool in _build_cached_tool_definitions(registry)
    }

    device = descriptions["network__operations__device__manage"]
    assert "probe=network" in device
    assert "read=network" in device
    assert "asset_id" in device
    assert "host" in device
    assert "commands" in device

    inspection = descriptions["network__operations__inspection"]
    assert "run=network" in inspection
    assert "get=network" in inspection
    assert "cancel=network" in inspection
    assert "asset_ids" in inspection
    assert "task_id" in inspection

    baseline = descriptions["network__operations__baseline"]
    assert "create=write" in baseline
    assert "confirm=write" in baseline
    assert "diff=read" in baseline
    assert "baseline_id" in baseline


def test_extension_routes_cover_asset_and_inspection_flow(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_PLATFORM_LOGIN_ENABLED", "false")
    from extensions.runtime import reset_extension_cache_for_tests
    reset_extension_cache_for_tests()
    from backend.main import create_app
    client = create_app().test_client()
    created = client.post("/api/extensions/network.operations/assets", json={
        "workspace_id": "default", "name": "R1", "host": "10.0.0.2",
        "username": "ops", "password": "secret-value", "vendor": "huawei",
    })
    assert created.status_code == 201
    listed = client.get("/api/extensions/network.operations/assets?workspace_id=default")
    assert listed.status_code == 200
    assert listed.get_json()["assets"][0]["name"] == "R1"
