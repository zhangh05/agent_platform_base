from __future__ import annotations

import json

from extensions.network_operations import service


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


def test_device_manage_is_registered_as_governed_connectivity_tool():
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    from core.tools.manifest_registry import get_manifest
    from core.tools.tool_namespace import metadata_for_tool

    assert "device.manage" in CANONICAL_REGISTRY
    manifest = get_manifest("device.manage")
    assert manifest is not None
    assert manifest.action_class == "network"
    assert manifest.risk_level == "medium"
    assert metadata_for_tool("device.manage")["category"] == "ops"


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
