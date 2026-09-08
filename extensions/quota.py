"""Per-extension concurrency leases.

There is deliberately no call-count or time-window quota here. A model-owned
agent loop must receive every tool result and decide whether to continue; a
platform-wide daily counter turns valid tracking work into an unrelated hard
failure.
"""

from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Any

class ExtensionQuotaError(RuntimeError):
    pass


# Deliberately process-local. The in-flight concurrency lease is accurate for
# this single-process deployment.
# Multi-worker deployments must provide a shared lease backend before relying
# on max_concurrency as a global limit.
_ACTIVE: dict[tuple[str, str], int] = {}
_ACTIVE_LOCK = threading.Lock()


@contextmanager
def extension_quota(extension_id: str, workspace_id: str, quotas: dict[str, Any] | None = None):
    limits = dict(quotas or {})
    concurrency = int(limits.get("max_concurrency") or 0)
    key = (extension_id, workspace_id)
    with _ACTIVE_LOCK:
        active = _ACTIVE.get(key, 0)
        if concurrency > 0 and active >= concurrency:
            raise ExtensionQuotaError("extension_concurrency_quota_exceeded")
        _ACTIVE[key] = active + 1
    try:
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
    with _ACTIVE_LOCK:
        active = _ACTIVE.get((extension_id, workspace_id), 0)
    return {
        "extension_id": extension_id,
        "workspace_id": workspace_id,
        "active": active,
        "concurrency_scope": "process_local",
        "limits": limits,
    }
