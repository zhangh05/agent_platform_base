"""Deterministic goal assertions for runtime turns."""
from __future__ import annotations

from typing import Any


def evaluate_goal_assertions(ctx, tool_results: list[Any]) -> dict[str, Any]:
    """Evaluate explicit assertions from durable tool facts."""
    extras = getattr(ctx, "extras", {}) or {}
    configured = extras.get("goal_assertions")
    assertions = [dict(item) for item in configured if isinstance(item, dict)] if isinstance(configured, list) else []
    if not assertions:
        return {"required": False, "status": "not_required", "assertions": []}
    results = {str(getattr(item, "call_id", "")): item for item in tool_results}
    evaluated: list[dict[str, Any]] = []
    for assertion in assertions:
        keys = [str(key) for key in assertion.get("required_call_keys") or []]
        missing_keys = [key for key in keys if key not in results]
        # Explicit assertions without keys apply to all observed operations.
        # Assertions with exact call IDs must never pass when a durable result
        # is absent.
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
