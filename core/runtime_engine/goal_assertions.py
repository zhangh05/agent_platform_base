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
        kind = str(assertion.get("kind") or "all_required_results_succeeded")
        if kind == "semantic_observation_collected":
            status = _semantic_observation_status(candidates, missing_keys, str(assertion.get("fact") or ""))
        elif kind == "evidence_claim_satisfied":
            status = _evidence_claim_status(tool_results, assertion)
        elif kind == "runtime_goal_satisfied":
            status = _runtime_goal_status(ctx, assertion)
        elif missing_keys or not candidates or any(bool(getattr(item, "execution_may_continue", False)) for item in candidates):
            status = "unknown"
        elif all(bool(getattr(item, "ok", False)) for item in candidates):
            status = "passed"
        else:
            status = "failed"
        evaluated.append({
            "assertion_id": str(assertion.get("assertion_id") or "goal_assertion"),
            "kind": kind,
            "status": status,
            "required_call_keys": keys,
            "missing_call_keys": missing_keys,
            **({
                "goal_id": str(assertion.get("goal_id") or ""),
            } if kind == "runtime_goal_satisfied" else {}),
            **({
                "goal_id": str(assertion.get("goal_id") or ""),
                "evidence_kind": str(assertion.get("evidence_kind") or ""),
                "target": dict(assertion.get("target") or {}),
                "fact": str(assertion.get("fact") or ""),
            } if kind == "evidence_claim_satisfied" else {}),
        })
    statuses = {item["status"] for item in evaluated}
    status = "passed" if statuses == {"passed"} else "unknown" if "unknown" in statuses else "failed"
    return {"required": True, "status": status, "assertions": evaluated}


def _runtime_goal_status(ctx, assertion: dict[str, Any]) -> str:
    goal_id = str(assertion.get("goal_id") or "")
    goal = next((
        item for item in (getattr(ctx, "extras", {}) or {}).get("recovery_goals") or []
        if isinstance(item, dict) and str(item.get("goal_id") or "") == goal_id
    ), None)
    if not isinstance(goal, dict):
        return "unknown"
    status = str(goal.get("status") or "pending").lower()
    if status == "passed":
        return "passed"
    if status == "blocked":
        return "failed"
    return "unknown"


def _semantic_observation_status(candidates: list[Any], missing_keys: list[str], fact: str) -> str:
    """Require literal device evidence, not just a transport-success response."""
    if missing_keys or not candidates or not fact:
        return "unknown"
    if any(bool(getattr(item, "execution_may_continue", False)) for item in candidates):
        return "unknown"
    for item in candidates:
        if not bool(getattr(item, "ok", False)):
            return "failed"
        output = getattr(item, "output", {})
        facts = output.get("facts") if isinstance(output, dict) else None
        value = facts.get(fact) if isinstance(facts, dict) else None
        if not isinstance(value, dict):
            return "unknown"
        if str(value.get("status") or "").lower() != "collected":
            return "failed" if str(value.get("status") or "").lower() == "unavailable" else "unknown"
    return "passed"


def _evidence_claim_status(tool_results: list[Any], assertion: dict[str, Any]) -> str:
    """Match evidence by goal identity, independently from one attempt ID."""
    expected_kind = str(assertion.get("evidence_kind") or "")
    expected_fact = str(assertion.get("fact") or "")
    expected_target = assertion.get("target") if isinstance(assertion.get("target"), dict) else {}
    matched: list[dict[str, Any]] = []
    positive_matched: list[dict[str, Any]] = []
    for result in tool_results:
        output = getattr(result, "output", {})
        if not isinstance(output, dict):
            continue
        for claim in output.get("evidence_claims") or []:
            if not isinstance(claim, dict) or str(claim.get("evidence_kind") or "") != expected_kind:
                continue
            if expected_fact and str(claim.get("fact") or "") != expected_fact:
                continue
            target = claim.get("target") if isinstance(claim.get("target"), dict) else {}
            if any(target.get(key) != value for key, value in expected_target.items()):
                continue
            matched.append(claim)
            if bool(getattr(result, "ok", False)) and not bool(getattr(result, "execution_may_continue", False)):
                positive_matched.append(claim)
    if any(str(item.get("status") or "").lower() in {"satisfied", "collected", "observed"} for item in positive_matched):
        return "passed"
    if any(str(item.get("status") or "").lower() in {"failed", "unavailable", "rejected"} for item in matched):
        return "failed"
    return "unknown"
