"""Generic long-task tracking helpers for SSOT Runtime.

Any capability can surface a ``tracking`` payload. Inspection is only one
producer; the runtime and frontend consume this generic shape:

    kind=long_task, domain=<capability>, task_id=<id>, status=<state>

Retry repeats a failed action. Tracking observes an already-created long task.
"""

from __future__ import annotations

from typing import Any


_TERMINAL_STATUSES = {
    "succeeded", "success", "completed", "complete", "done",
    "failed", "error", "cancelled", "canceled", "partial", "crashed",
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "done", "terminal"}
    return False


def _as_non_negative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def extract_tracking_payload(data: Any) -> dict[str, Any]:
    """Return a tracking payload from a nested tool result, if present."""
    if not isinstance(data, dict):
        return {}
    direct = data.get("tracking")
    if isinstance(direct, dict) and direct:
        return normalize_tracking_payload(direct)
    for key in ("output", "content", "data", "task", "result"):
        nested = data.get(key)
        if isinstance(nested, dict):
            found = extract_tracking_payload(nested)
            if found:
                return found
    return {}


def normalize_tracking_payload(tracking: dict[str, Any]) -> dict[str, Any]:
    """Normalize producer-specific tracking into the SSOT metadata contract."""
    summary = tracking.get("summary") if isinstance(tracking.get("summary"), dict) else {}
    progress = tracking.get("progress") if isinstance(tracking.get("progress"), dict) else {}
    policy = tracking.get("policy") if isinstance(tracking.get("policy"), dict) else {}
    task_id = tracking.get("task_id") or summary.get("task_id") or ""
    status = str(tracking.get("status") or summary.get("status") or "").strip()
    done = (
        _as_bool(tracking.get("done"))
        or _as_bool(tracking.get("terminal"))
        or status.lower() in _TERMINAL_STATUSES
    )
    poll_arguments = tracking.get("poll_arguments")
    if not isinstance(poll_arguments, dict):
        poll_arguments = {}
    return {
        "kind": tracking.get("kind") or "long_task",
        "domain": tracking.get("domain") or tracking.get("capability") or "",
        "task_id": task_id,
        "status": status,
        "done": done,
        "terminal": done,
        "mode": policy.get("mode", "") or tracking.get("mode", ""),
        "poll_count": _as_non_negative_int(tracking.get("poll_count")),
        "same_status_count": _as_non_negative_int(tracking.get("same_status_count")),
        "stall_risk": _as_bool(tracking.get("stall_risk")),
        "next_poll_seconds": _as_non_negative_int(tracking.get("next_poll_seconds")),
        "suggested_next_action": tracking.get("suggested_next_action", ""),
        "poll_action": tracking.get("poll_action", "get"),
        "poll_arguments": dict(poll_arguments),
        "progress": progress,
        "summary": summary,
        "raw": tracking,
    }
