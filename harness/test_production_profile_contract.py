"""Production profile is fail-closed, observable and reproducible."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_docker_context_excludes_local_runtimes_and_generated_data():
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {".venv*", "/venv", "/runtime", "frontend/node_modules", "workspaces"} <= ignored


def test_compose_profile_has_required_runtime_boundaries():
    profile = yaml.safe_load((ROOT / "deployment" / "compose.production.yml").read_text(encoding="utf-8"))
    services = profile["services"]
    assert {"gateway", "frontend", "backend", "worker", "postgres", "redis", "minio", "prometheus", "alertmanager", "grafana"} <= set(services)
    assert "ports" not in services["backend"]
    assert "ports" not in services["frontend"]
    assert services["gateway"]["ports"]
    assert services["backend"]["healthcheck"]
    assert services["backend"]["cap_drop"] == ["ALL"]
    assert services["frontend"]["read_only"] is True
    assert {"master_key", "session_secret", "login_password", "api_token"} <= set(profile["secrets"])
    assert profile["x-backend-environment"]["LZCORE_MASTER_KEY_FILE"] == "/run/secrets/master_key"


def test_observability_profile_has_alerts_dashboard_and_runbook():
    prometheus = yaml.safe_load((ROOT / "deployment" / "observability" / "prometheus.yml").read_text(encoding="utf-8"))
    alerts = yaml.safe_load((ROOT / "deployment" / "observability" / "alerts.yml").read_text(encoding="utf-8"))
    assert prometheus["scrape_configs"][0]["bearer_token_file"] == "/run/secrets/api_token"
    names = {rule["alert"] for group in alerts["groups"] for rule in group["rules"]}
    assert {
        "LZCoreTargetDown",
        "LZCoreHighHttp5xxRate",
        "LZCoreToolFailures",
        "LZCoreJobFailures",
    } <= names
    assert (ROOT / "deployment" / "observability" / "grafana-provisioning" / "dashboards" / "json" / "lzcore.json").is_file()
    assert (ROOT / "docs" / "OPERATIONS_RUNBOOK.md").is_file()


def test_secret_files_are_supported(monkeypatch, tmp_path):
    from backend.core.auth import _get_api_token, _get_login_password, _secret_value

    files = {}
    for name, value in {
        "LZCORE_API_TOKEN": "a" * 32,
        "LZCORE_LOGIN_PASSWORD": "b" * 16,
        "LZCORE_SESSION_SECRET": "c" * 40,
    }.items():
        path = tmp_path / name.lower()
        path.write_text(value, encoding="utf-8")
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(f"{name}_FILE", str(path))
        files[name] = value
    assert _get_api_token() == files["LZCORE_API_TOKEN"]
    assert _get_login_password() == files["LZCORE_LOGIN_PASSWORD"]
    assert _secret_value("LZCORE_SESSION_SECRET") == files["LZCORE_SESSION_SECRET"]


def test_oidc_claim_mapping_and_disabled_routes(monkeypatch):
    from backend.core.oidc import _username_from_claims
    from backend.main import create_app

    monkeypatch.delenv("LZCORE_OIDC_ENABLED", raising=False)
    monkeypatch.setenv("LZCORE_OIDC_USERNAME_CLAIM", "preferred_username")
    assert _username_from_claims({"preferred_username": "alice", "sub": "subject"}) == "alice"
    assert _username_from_claims({"email": "alice@example.com"}) == "alice@example.com"
    with pytest.raises(ValueError):
        _username_from_claims({"preferred_username": "invalid user"})
    client = create_app().test_client()
    assert client.get("/api/auth/oidc/start").status_code == 404
    assert client.get("/api/auth/oidc/callback").status_code == 404
