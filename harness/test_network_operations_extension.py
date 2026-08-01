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
    assert "password" not in json.dumps(service.list_assets("default"))
    persisted = (tmp_path / "workspaces" / "_runtime" / "secrets" / "encrypted.json").read_text()
    assert "sensitive-password" not in persisted


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
