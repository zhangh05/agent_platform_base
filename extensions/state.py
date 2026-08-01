"""Durable lifecycle and health state for installed extensions."""

from __future__ import annotations

from typing import Any

from storage.atomic_io import atomic_write_json, safe_read_json
from storage.locking import FileLock
from storage.records import runtime_record_file
from storage.time_utils import now_iso


def _path():
    return runtime_record_file("extensions", "state.json", create_parent=True)


def _read() -> dict[str, Any]:
    value = safe_read_json(_path(), {})
    return value if isinstance(value, dict) else {}


def get_extension_state(extension_id: str, *, default_enabled: bool = True) -> dict[str, Any]:
    record = dict(_read().get(extension_id) or {})
    return {
        "extension_id": extension_id,
        "enabled": bool(record.get("enabled", default_enabled)),
        "status": str(record.get("status") or "ready"),
        "failure_count": int(record.get("failure_count") or 0),
        "last_error": str(record.get("last_error") or ""),
        "schema_versions": dict(record.get("schema_versions") or {}),
        "updated_at": str(record.get("updated_at") or ""),
    }


def update_extension_state(extension_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    path = _path()
    with FileLock(path.with_name("state.lock")):
        data = _read()
        current = dict(data.get(extension_id) or {})
        current.update(patch)
        current["updated_at"] = now_iso()
        data[extension_id] = current
        atomic_write_json(path, data)
    return get_extension_state(extension_id)


def set_extension_enabled(extension_id: str, enabled: bool) -> dict[str, Any]:
    return update_extension_state(extension_id, {
        "enabled": bool(enabled),
        "status": "ready" if enabled else "disabled",
        "failure_count": 0,
        "last_error": "",
    })


def record_extension_success(extension_id: str) -> None:
    update_extension_state(extension_id, {"status": "ready", "failure_count": 0, "last_error": ""})


def record_extension_failure(extension_id: str, error: str, *, threshold: int = 5) -> dict[str, Any]:
    state = get_extension_state(extension_id)
    failures = state["failure_count"] + 1
    patch: dict[str, Any] = {
        "failure_count": failures,
        "last_error": str(error)[:500],
        "status": "degraded" if failures < threshold else "quarantined",
    }
    if failures >= threshold:
        patch["enabled"] = False
    return update_extension_state(extension_id, patch)


def set_workspace_schema_version(extension_id: str, workspace_id: str, version: int) -> dict[str, Any]:
    state = get_extension_state(extension_id)
    versions = dict(state["schema_versions"])
    versions[workspace_id] = max(0, int(version))
    return update_extension_state(extension_id, {"schema_versions": versions})
