from __future__ import annotations

import pytest

from extensions.quota import ExtensionQuotaError, extension_quota, quota_status
from extensions.state import get_extension_state, record_extension_failure, set_extension_enabled


def test_extension_quota_is_workspace_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    limits = {"daily_calls": 2, "max_concurrency": 1}
    with extension_quota("vendor.sample", "workspace_a", limits):
        status = quota_status("vendor.sample", "workspace_a", limits)
        assert status["active"] == 1
        with pytest.raises(ExtensionQuotaError, match="concurrency"):
            with extension_quota("vendor.sample", "workspace_a", limits):
                pass
    with extension_quota("vendor.sample", "workspace_a", limits):
        pass
    with pytest.raises(ExtensionQuotaError, match="daily"):
        with extension_quota("vendor.sample", "workspace_a", limits):
            pass
    with extension_quota("vendor.sample", "workspace_b", limits):
        pass


def test_failure_threshold_quarantines_extension(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    set_extension_enabled("vendor.sample", True)
    for index in range(5):
        state = record_extension_failure("vendor.sample", f"failure-{index}")
    assert state["enabled"] is False
    assert state["status"] == "quarantined"
    assert get_extension_state("vendor.sample")["last_error"] == "failure-4"


def test_extension_routes_enforce_role_and_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("LZCORE_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("LZCORE_MASTER_KEY", "test-extension-master-key")
    monkeypatch.delenv("LZCORE_LOGIN_USERNAME", raising=False)
    monkeypatch.delenv("LZCORE_LOGIN_PASSWORD", raising=False)
    from backend.core.identity import upsert_user
    upsert_user("viewer", "password", "viewer", "default", ["default"])
    upsert_user("operator", "password", "operator", "default", ["default"])
    upsert_user("admin", "password", "admin", "default", ["default"])
    from extensions.runtime import reset_extension_cache_for_tests
    reset_extension_cache_for_tests()
    from backend.main import create_app
    app = create_app()
    app.config.update(TESTING=True)
    origin = {"Origin": "http://localhost:5273"}

    viewer = app.test_client()
    viewer.post("/api/auth/login", json={"username": "viewer", "password": "password"}, headers=origin)
    denied = viewer.post("/api/extensions/network.operations/assets", json={"workspace_id": "default"}, headers=origin)
    assert denied.status_code == 403
    assert denied.get_json()["error"] == "extension_write_forbidden"
    assert viewer.get("/api/admin/backups", headers=origin).status_code == 403
    assert viewer.get(
        "/api/admin/approval-continuations?workspace_id=default", headers=origin
    ).status_code == 403
    assert viewer.get("/api/admin/operation-ledger?workspace_id=default", headers=origin).status_code == 403

    operator = app.test_client()
    operator.post("/api/auth/login", json={"username": "operator", "password": "password"}, headers=origin)
    created = operator.post("/api/extensions/network.operations/assets", json={
        "workspace_id": "default", "name": "R1", "host": "10.0.0.1",
        "username": "ops", "password": "secret", "vendor": "h3c",
    }, headers=origin)
    assert created.status_code == 201
    assert operator.post("/api/extensions/network.operations/disable", headers=origin).status_code == 403
    assert operator.post("/api/extensions/repository/publish", headers=origin).status_code == 403

    admin = app.test_client()
    admin.post("/api/auth/login", json={"username": "admin", "password": "password"}, headers=origin)
    assert admin.get("/api/admin/backups", headers=origin).status_code == 200
    continuation_status = admin.get(
        "/api/admin/approval-continuations?workspace_id=default", headers=origin
    )
    assert continuation_status.status_code == 200
    assert continuation_status.get_json()["ok"] is True
    ledger_status = admin.get("/api/admin/operation-ledger?workspace_id=default", headers=origin)
    assert ledger_status.status_code == 200
    assert ledger_status.get_json()["ok"] is True
    assert admin.get(
        "/api/admin/operation-ledger?workspace_id=default&status=not-a-state",
        headers=origin,
    ).status_code == 400
    assert admin.post("/api/extensions/repository/publish", headers=origin).status_code == 400
    assert admin.post("/api/extensions/network.operations/disable", headers=origin).status_code == 200
    blocked = admin.get("/api/extensions/network.operations/assets?workspace_id=default", headers=origin)
    assert blocked.status_code == 409
    assert admin.post("/api/extensions/network.operations/enable", headers=origin).status_code == 200
