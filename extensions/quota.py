"""Per-extension workspace quotas and concurrency leases."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import threading
from typing import Any

from storage.atomic_io import atomic_write_json, safe_read_json
from storage.locking import FileLock
from storage.records import runtime_record_file


class ExtensionQuotaError(RuntimeError):
    pass


_ACTIVE: dict[tuple[str, str], int] = {}
_ACTIVE_LOCK = threading.Lock()


def _path():
    return runtime_record_file("extensions", "quota.json", create_parent=True)


def _day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _consume_daily(extension_id: str, workspace_id: str, limit: int) -> int:
    if limit <= 0:
        return 0
    path = _path()
    key = f"{extension_id}:{workspace_id}:{_day()}"
    with FileLock(path.with_name("quota.lock")):
        data = safe_read_json(path, {})
        if not isinstance(data, dict):
            data = {}
        count = int(data.get(key) or 0)
        if count >= limit:
            raise ExtensionQuotaError("extension_daily_quota_exceeded")
        data = {item_key: value for item_key, value in data.items() if item_key.endswith(_day())}
        data[key] = count + 1
        atomic_write_json(path, data)
        return count + 1


@contextmanager
def extension_quota(extension_id: str, workspace_id: str, quotas: dict[str, Any] | None = None):
    limits = dict(quotas or {})
    daily = int(limits.get("daily_calls") or 0)
    concurrency = int(limits.get("max_concurrency") or 0)
    key = (extension_id, workspace_id)
    with _ACTIVE_LOCK:
        active = _ACTIVE.get(key, 0)
        if concurrency > 0 and active >= concurrency:
            raise ExtensionQuotaError("extension_concurrency_quota_exceeded")
        _ACTIVE[key] = active + 1
    try:
        _consume_daily(extension_id, workspace_id, daily)
        yield
    finally:
        with _ACTIVE_LOCK:
            remaining = max(0, _ACTIVE.get(key, 1) - 1)
            if remaining:
                _ACTIVE[key] = remaining
            else:
                _ACTIVE.pop(key, None)


def quota_status(extension_id: str, workspace_id: str, quotas: dict[str, Any] | None = None) -> dict[str, Any]:
    limits = dict(quotas or {})
    data = safe_read_json(_path(), {})
    key = f"{extension_id}:{workspace_id}:{_day()}"
    with _ACTIVE_LOCK:
        active = _ACTIVE.get((extension_id, workspace_id), 0)
    return {
        "extension_id": extension_id,
        "workspace_id": workspace_id,
        "day": _day(),
        "daily_calls": int(data.get(key) or 0) if isinstance(data, dict) else 0,
        "active": active,
        "limits": limits,
    }
