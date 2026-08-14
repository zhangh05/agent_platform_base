"""Deterministic goal assertions for selected high-risk runtime turns."""
from __future__ import annotations

from typing import Any


def _default_assertions(ctx) -> list[dict[str, Any]]:
    continuation_id = str((getattr(ctx, "extras", {}) or {}).get("approval_continuation_id") or "")
    if not continuation_id:
        return []
    approved = list((getattr(ctx, "extras", {}) or {}).get("approved_tool_call_ids") or [])
    return [{
        "assertion_id": "approved_operations_succeeded",
        "kind": "all_approved_operations_succeeded",
        "continuation_id": continuation_id,
        "required_call_keys": approved,
    }]


def evaluate_goal_assertions(ctx, tool_results: list[Any]) -> dict[str, Any]:
    """Evaluate only explicit or approval-bound assertions from durable facts."""
    extras = getattr(ctx, "extras", {}) or {}
    configured = extras.get("goal_assertions")
    assertions = [dict(item) for item in configured if isinstance(item, dict)] if isinstance(configured, list) else _default_assertions(ctx)
    if not assertions:
        return {"required": False, "status": "not_required", "assertions": []}
    results = {str(getattr(item, "call_id", "")): item for item in tool_results}
    evaluated: list[dict[str, Any]] = []
    for assertion in assertions:
        keys = [str(key) for key in assertion.get("required_call_keys") or []]
        missing_keys = [key for key in keys if key not in results]
        # Explicit assertions without keys apply to all observed operations.
        # Approval-bound assertions always carry exact call IDs and must never
        # pass when one of those durable results is absent.
        candidates = [results[key] for key in keys if key in results] if keys else list(results.values())
        if missing_keys or not candidates or any(bool(getattr(item, "execution_may_continue", False)) for item in candidates):
            status = "unknown"
        elif all(bool(getattr(item, "ok", False)) for item in candidates):
            status = "passed"
        else:
            status = "failed"
        evaluated.append({
            "assertion_id": str(assertion.get("assertion_id") or "goal_assertion"),
            "kind": str(assertion.get("kind") or "all_required_results_succeeded"),
            "status": status,
            "required_call_keys": keys,
            "missing_call_keys": missing_keys,
        })
    statuses = {item["status"] for item in evaluated}
    status = "passed" if statuses == {"passed"} else "unknown" if "unknown" in statuses else "failed"
    return {"required": True, "status": status, "assertions": evaluated}
