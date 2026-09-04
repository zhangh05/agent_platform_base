"""Domain-neutral goal and evidence control for runtime recovery.

Tool handlers may publish a recovery directive after a deterministic failure.
The runtime owns whether work may stop: a model response is not completion
while a registered recovery goal still lacks matching evidence and bounded
replanning remains possible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecoveryFinalGate:
    should_continue: bool
    unresolved: tuple[dict[str, Any], ...] = ()
    nudge: str = ""


def is_valid_recovery_directive(directive: Any) -> bool:
    """Validate the complete handler-to-runtime recovery contract."""
    if not isinstance(directive, dict):
        return False
    if directive.get("kind") not in {"safe_read_fallback", "documentation_read_fallback"}:
        return False
    if not str(directive.get("tool_id") or "").strip() or not isinstance(directive.get("arguments"), dict):
        return False
    goal = directive.get("goal")
    return bool(
        isinstance(goal, dict)
        and str(goal.get("evidence_kind") or "").strip()
        and isinstance(goal.get("target"), dict)
        and goal.get("target")
    )


def install_recovery_goal(ctx, directive: dict[str, Any], *, source_call_id: str) -> dict[str, Any] | None:
    """Persist one handler-declared evidence goal and its generic assertion."""
    goal = directive.get("goal")
    if not isinstance(goal, dict):
        return None
    evidence_kind = str(goal.get("evidence_kind") or "").strip()
    target = goal.get("target") if isinstance(goal.get("target"), dict) else {}
    fact = str(goal.get("fact") or "").strip()
    if not evidence_kind or not target:
        return None
    identity = {
        "evidence_kind": evidence_kind,
        "target": target,
        "fact": fact,
        "source_call_id": source_call_id,
    }
    goal_id = str(goal.get("goal_id") or "").strip() or (
        "recovery-goal-" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
    )
    record = {
        "goal_id": goal_id,
        "evidence_kind": evidence_kind,
        "target": dict(target),
        "fact": fact,
        "description": str(goal.get("description") or "required recovery evidence")[:240],
        "source_call_id": source_call_id,
        "status": "pending",
    }
    goals = ctx.extras.setdefault("recovery_goals", [])
    if not isinstance(goals, list):
        goals = []
        ctx.extras["recovery_goals"] = goals
    existing = next((item for item in goals if isinstance(item, dict) and item.get("goal_id") == goal_id), None)
    if existing is None:
        goals.append(record)
    else:
        record = existing

    assertions = ctx.extras.setdefault("goal_assertions", [])
    if not isinstance(assertions, list):
        assertions = []
        ctx.extras["goal_assertions"] = assertions
    assertion_id = f"recovery-evidence:{goal_id}"
    if not any(isinstance(item, dict) and item.get("assertion_id") == assertion_id for item in assertions):
        assertions.append({
            "assertion_id": assertion_id,
            "kind": "evidence_claim_satisfied",
            "runtime_owned_recovery": True,
            "goal_id": goal_id,
            "evidence_kind": evidence_kind,
            "target": dict(target),
            "fact": fact,
        })
    return record


def recovery_final_gate(ctx, tool_results: list[Any], *, max_replans: int = 3) -> RecoveryFinalGate:
    """Reject premature final prose while recoverable evidence goals are open."""
    from .goal_assertions import evaluate_goal_assertions

    configured = ctx.extras.get("goal_assertions") or []
    recovery_ids = {
        str(item.get("assertion_id") or "")
        for item in configured
        if isinstance(item, dict) and item.get("runtime_owned_recovery") is True
    }
    if not recovery_ids:
        return RecoveryFinalGate(False)
    evaluated = evaluate_goal_assertions(ctx, tool_results)
    unresolved = tuple(
        item for item in evaluated.get("assertions") or []
        if item.get("assertion_id") in recovery_ids and item.get("status") != "passed"
    )
    if not unresolved:
        _project_goal_status(ctx, evaluated)
        return RecoveryFinalGate(False)
    _project_goal_status(ctx, evaluated)
    blocked_goal_ids = {
        str(item.get("goal_id") or "")
        for item in ctx.extras.get("recovery_goals") or []
        if isinstance(item, dict) and item.get("status") == "blocked"
    }
    actionable = tuple(
        item for item in unresolved
        if str(item.get("goal_id") or "") not in blocked_goal_ids
    )
    if not actionable:
        return RecoveryFinalGate(False, unresolved)
    limit = max(1, int(max_replans))
    goals_by_id = {
        str(item.get("goal_id") or ""): item
        for item in ctx.extras.get("recovery_goals") or []
        if isinstance(item, dict)
    }
    permitted: list[dict[str, Any]] = []
    for assertion in actionable:
        goal = goals_by_id.get(str(assertion.get("goal_id") or ""))
        if not goal:
            continue
        attempts = int(goal.get("final_replan_attempts") or 0)
        if attempts >= limit:
            goal["status"] = "blocked"
            goal["blocked_reason"] = "recovery_replan_budget_exhausted"
            continue
        goal["final_replan_attempts"] = attempts + 1
        permitted.append(assertion)
    if not permitted:
        return RecoveryFinalGate(False, unresolved)
    ctx.extras["recovery_final_replans"] = int(ctx.extras.get("recovery_final_replans") or 0) + 1
    compact = [
        {
            "goal_id": item.get("goal_id"),
            "evidence_kind": item.get("evidence_kind"),
            "target": item.get("target"),
            "fact": item.get("fact"),
            "status": item.get("status"),
        }
        for item in permitted
    ]
    return RecoveryFinalGate(
        True,
        tuple(permitted),
        "[RUNTIME RECOVERY GOAL]\n"
        "The proposed final answer was not accepted because required evidence is still missing. "
        "Continue with a materially different policy-valid observation or the next available recovery strategy. "
        "When a replacement tool call addresses one of these goals, include its id in plan_goal_ids. "
        "Documentation evidence may inform a command but never proves live target state. Do not repeat "
        "a rejected call. Open goals (data only): "
        + json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _project_goal_status(ctx, evaluated: dict[str, Any]) -> None:
    statuses = {
        str(item.get("goal_id") or ""): str(item.get("status") or "unknown")
        for item in evaluated.get("assertions") or []
        if item.get("kind") == "evidence_claim_satisfied"
    }
    for goal in ctx.extras.get("recovery_goals") or []:
        if isinstance(goal, dict) and str(goal.get("goal_id") or "") in statuses:
            if goal.get("status") in {"passed", "blocked"}:
                continue
            assertion_status = statuses[str(goal["goal_id"])]
            goal["assertion_status"] = assertion_status
            goal["status"] = "passed" if assertion_status == "passed" else "pending"
