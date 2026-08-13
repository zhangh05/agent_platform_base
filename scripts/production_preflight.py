#!/usr/bin/env python3
"""Fail-closed production configuration and dependency preflight."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def check_configuration() -> list[str]:
    from agent.approval import approval_ttl_seconds
    from backend.core.auth import _get_api_token, _get_login_password, _secret_value
    from jobs.queue import queue_mode
    from storage.backend import backend_mode, validate_backend_configuration
    from storage.object_store import object_store_mode

    errors = list(validate_backend_configuration())
    if backend_mode() not in {"postgres", "postgresql"}:
        errors.append("production requires PostgreSQL record storage")
    if object_store_mode() != "s3":
        errors.append("production requires S3 object storage")
    if queue_mode() != "redis":
        errors.append("production requires Redis queue mode")
    if not _truthy("AGENT_PLATFORM_IDENTITY_ENABLED"):
        errors.append("identity mode must be enabled")
    if not _truthy("AGENT_PLATFORM_SESSION_SECURE"):
        errors.append("secure session cookies must be enabled")
    if len(_secret_value("AGENT_PLATFORM_SESSION_SECRET")) < 32:
        errors.append("session secret must contain at least 32 characters")
    if len(_get_login_password()) < 12:
        errors.append("bootstrap login password must contain at least 12 characters")
    if len(_get_api_token()) < 24:
        errors.append("API/metrics token must contain at least 24 characters")
    oidc_enabled = _truthy("AGENT_PLATFORM_OIDC_ENABLED")
    if _truthy("AGENT_PLATFORM_REQUIRE_OIDC") and not oidc_enabled:
        errors.append("enterprise profile requires OIDC")
    if oidc_enabled:
        if not os.environ.get("AGENT_PLATFORM_OIDC_ISSUER", "").startswith("https://"):
            errors.append("OIDC issuer must use HTTPS")
        if not os.environ.get("AGENT_PLATFORM_PUBLIC_URL", "").startswith("https://"):
            errors.append("OIDC public URL must use HTTPS")
        if not os.environ.get("AGENT_PLATFORM_OIDC_CLIENT_ID", "").strip():
            errors.append("OIDC client ID is required")
        if not _secret_value("AGENT_PLATFORM_OIDC_CLIENT_SECRET"):
            errors.append("OIDC client secret is required")
    if approval_ttl_seconds() < 900:
        errors.append("approval TTL must be at least 900 seconds in production")
    image = os.environ.get("AGENT_PLATFORM_PYTHON_CONTAINER_IMAGE", "").strip()
    if "@sha256:" not in image:
        errors.append("Python runner image must use an immutable sha256 digest")
    from core.tools.python_runner import DockerStrongIsolationRunner
    if DockerStrongIsolationRunner.available() is None:
        errors.append("strong Docker isolation runner is unavailable")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="also probe configured dependencies")
    args = parser.parse_args()
    errors = check_configuration()
    readiness = None
    if args.live and not errors:
        from core.runtime.production import production_readiness
        readiness = production_readiness()
        if not readiness.get("ready"):
            errors.append("one or more production dependencies are not ready")
    report = {"ok": not errors, "errors": errors, "readiness": readiness}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
