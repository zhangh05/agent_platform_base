"""Pure preflight tests for start.sh network exposure policy."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "scripts" / "startup_security.sh"


def _run_policy(backend_host: str, frontend_host: str, extra_env: dict[str, str] | None = None):
    env = os.environ.copy()
    for key in (
        "LZCORE_AUTH_ENABLED",
        "LZCORE_API_TOKEN",
        "LZCORE_LOGIN_USERNAME",
        "LZCORE_LOGIN_PASSWORD",
        "LZCORE_IDENTITY_ENABLED",
        "LZCORE_ALLOW_UNAUTHENTICATED_NETWORK",
    ):
        env.pop(key, None)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", "-c", 'source "$1"; startup_security_validate_network_exposure "$2" "$3"', "--", str(POLICY), backend_host, frontend_host],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_loopback_hosts_are_allowed_without_authentication():
    result = _run_policy("127.0.0.1", "localhost")
    assert result.returncode == 0


def test_network_backend_without_authentication_is_rejected():
    result = _run_policy("0.0.0.0", "127.0.0.1")
    assert result.returncode != 0
    assert "Refusing network listener" in result.stderr


def test_network_frontend_proxy_without_authentication_is_rejected():
    result = _run_policy("127.0.0.1", "0.0.0.0")
    assert result.returncode != 0


def test_network_listener_allows_effective_api_token_authentication():
    result = _run_policy(
        "0.0.0.0",
        "0.0.0.0",
        {"LZCORE_AUTH_ENABLED": "true", "LZCORE_API_TOKEN": "test-token"},
    )
    assert result.returncode == 0
    assert "api_token authentication" in result.stdout


def test_network_listener_rejects_empty_api_token_even_when_flag_is_set():
    result = _run_policy("0.0.0.0", "0.0.0.0", {"LZCORE_AUTH_ENABLED": "true"})
    assert result.returncode != 0


def test_network_listener_allows_login_or_identity_authentication():
    login = _run_policy(
        "0.0.0.0",
        "0.0.0.0",
        {"LZCORE_LOGIN_USERNAME": "tester", "LZCORE_LOGIN_PASSWORD": "secret"},
    )
    identity = _run_policy("0.0.0.0", "0.0.0.0", {"LZCORE_IDENTITY_ENABLED": "true"})
    assert login.returncode == 0
    assert identity.returncode == 0


def test_explicitly_disabled_login_is_not_treated_as_effective_authentication():
    result = _run_policy(
        "0.0.0.0",
        "0.0.0.0",
        {
            "LZCORE_LOGIN_ENABLED": "false",
            "LZCORE_LOGIN_USERNAME": "retained-user",
            "LZCORE_LOGIN_PASSWORD": "retained-password",
        },
    )
    assert result.returncode != 0


def test_direct_backend_listener_uses_the_same_fail_closed_policy(monkeypatch):
    from backend.core.auth import validate_network_listener

    for key in (
        "LZCORE_AUTH_ENABLED",
        "LZCORE_API_TOKEN",
        "LZCORE_LOGIN_ENABLED",
        "LZCORE_LOGIN_USERNAME",
        "LZCORE_LOGIN_PASSWORD",
        "LZCORE_IDENTITY_ENABLED",
        "LZCORE_ALLOW_UNAUTHENTICATED_NETWORK",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="Refusing non-loopback backend listener"):
        validate_network_listener("0.0.0.0")


def test_dangerous_explicit_override_warns_and_allows_network_listener():
    result = _run_policy(
        "0.0.0.0",
        "0.0.0.0",
        {"LZCORE_ALLOW_UNAUTHENTICATED_NETWORK": "true"},
    )
    assert result.returncode == 0
    assert "DANGER" in result.stderr
