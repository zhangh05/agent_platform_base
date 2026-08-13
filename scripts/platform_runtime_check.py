"""Validate production adapter configuration without making network calls."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs.queue import queue_configuration
from storage.backend import backend_mode, validate_backend_configuration
from storage.object_store import object_store_mode


def _secret_value(name: str) -> str:
    direct = os.environ.get(name, "").strip()
    if direct:
        return direct
    path = os.environ.get(f"{name}_FILE", "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


if __name__ == "__main__":
    errors = validate_backend_configuration()
    queue = queue_configuration()
    if queue["mode"] == "redis" and importlib.util.find_spec("redis") is None:
        errors.append("redis package is required for redis queue")
    event_mode = os.environ.get("AGENT_PLATFORM_EVENT_BUS_MODE", "inprocess").strip().lower()
    if event_mode == "redis":
        event_url = os.environ.get("AGENT_PLATFORM_EVENT_BUS_URL") or os.environ.get("AGENT_PLATFORM_QUEUE_URL")
        if not event_url:
            errors.append("AGENT_PLATFORM_EVENT_BUS_URL or AGENT_PLATFORM_QUEUE_URL is required for redis events")
        if importlib.util.find_spec("redis") is None:
            errors.append("redis package is required for redis events")
    if object_store_mode() == "s3" and importlib.util.find_spec("boto3") is None:
        errors.append("boto3 package is required for S3 storage")
    if backend_mode() in {"postgres", "postgresql"} and importlib.util.find_spec("psycopg") is None:
        errors.append("psycopg package is required for PostgreSQL storage")
    if os.environ.get("AGENT_PLATFORM_IDENTITY_ENABLED", "false").lower() in {"1", "true", "yes", "on"} and not _secret_value("AGENT_PLATFORM_SESSION_SECRET"):
        errors.append("AGENT_PLATFORM_SESSION_SECRET is required in identity mode")
    if os.environ.get("AGENT_PLATFORM_IDENTITY_ENABLED", "false").lower() in {"1", "true", "yes", "on"} and len(_secret_value("AGENT_PLATFORM_MASTER_KEY")) < 16:
        errors.append("AGENT_PLATFORM_MASTER_KEY (16+ characters) is required in identity mode")
    result = {"record_storage": backend_mode(), "object_storage": object_store_mode(), "queue": queue, "event_bus": event_mode, "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["errors"] else 0)
