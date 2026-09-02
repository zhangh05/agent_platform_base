"""Domain-neutral goal-loop control for failed tool observations.

The model proposes actions, but the runtime owns whether a failed observation
has been resolved.  Every canonical tool result is normalized here.  A
recoverable failed read installs a bounded goal; later calls must either name
that goal explicitly through ``plan_goal_ids`` or produce a compatible
successful observation.  This keeps the policy generic while allowing domain
extensions to publish richer evidence goals and deterministic recovery plans.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable


_TERMINAL_FAILURE_CODES = frozenset({
    "CANCELLED_BY_USER",
    "POLICY_BLOCKED",
    "AUTHORIZATION_FAILED",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "CREDENTIAL_ACCESS",
    "PLAN_STOPPED",
})

_TARGET_KEYS = (
    "workspace_id", "device_id", "connection_id", "resource_id", "artifact_id",
    "document_id", "file_id", "filepath", "path", "url", "location", "task_id",
    "job_id", "subtask_id", "host", "address", "query",
)

_MAX_TARGET_TEXT = 240


def observe_tool_round(
    ctx,
    tool_calls: list[Any],
    tool_results: list[Any],
    *,
    is_read_only_call: Callable[[Any], bool],
) -> None:
    """Normalize one tool round and advance generic recovery goals."""
    observations = ctx.extras.setdefault("goal_loop_observations", [])
    if not isinstance(observations, list):
        observations = []
        ctx.extras["goal_loop_observations"] = observations

    for call, result in zip(tool_calls, tool_results):
        output = result.output if isinstance(getattr(result, "output", None), dict) else {}
        error_code = str(
            getattr(result, "error_code", "") or output.get("error_code") or ""
        ).strip().upper()
        target = _target_from_arguments(getattr(call, "arguments", {}) or {})
        action = str((getattr(call, "arguments", {}) or {}).get("action") or "observe")
        observation = {
            "call_id": str(getattr(call, "id", "") or getattr(result, "call_id", "")),
            "tool_id": str(getattr(call, "name", "") or getattr(result, "tool_name", "")).replace("__", "."),
            "action": action,
            "target": target,
            "status": "succeeded" if bool(getattr(result, "ok", False)) else "failed",
            "failure_class": "" if bool(getattr(result, "ok", False)) else _failure_class(error_code, str(getattr(result, "error", "") or "")),
            "error_code": error_code,
            "read_only": bool(is_read_only_call(call)),
            "execution_may_continue": bool(getattr(result, "execution_may_continue", False) or output.get("execution_may_continue")),
            "goal_ids": list(getattr(call, "goal_ids", None) or []),
        }
        observations.append(observation)
        del observations[:-256]

        if bool(getattr(result, "ok", False)):
            if observation["read_only"] and not observation["execution_may_continue"]:
                _resolve_goals_from_success(ctx, call, observation)
            continue
        if _has_domain_recovery(output):
            continue
        if getattr(call, "goal_ids", None):
            _mark_explicit_goals_blocked(ctx, call, observation)
            continue
        if _should_install_goal(call, observation, output):
            _install_generic_goal(ctx, call, observation)


def goal_loop_nudge(ctx) -> str:
    """Return compact model guidance for currently open generic goals."""
    open_goals = [
        goal for goal in ctx.extras.get("recovery_goals") or []
        if isinstance(goal, dict)
        and goal.get("goal_type") == "tool_recovery"
        and goal.get("status") == "pending"
    ]
    if not open_goals:
        return ""
    payload = [
        {
            "goal_id": goal.get("goal_id"),
            "tool_id": goal.get("source_tool_id"),
            "action": goal.get("fact"),
            "target": goal.get("target"),
            "failure_class": goal.get("failure_class"),
            "attempts": goal.get("attempts"),
            "max_attempts": goal.get("max_attempts"),
            "strategy_candidates": [
                item.get("strategy_id") for item in goal.get("strategy_candidates") or []
                if isinstance(item, dict) and item.get("strategy_id")
            ],
        }
        for goal in open_goals
    ]
    return (
        "[RUNTIME GOAL LOOP] A recoverable observation goal remains open. Do not finalize merely "
        "because one tool call failed. Correct the arguments, narrow the request, or choose a "
        "different capability. Add plan_goal_ids containing the relevant goal_id to every replacement "
        "call so the runtime can reconcile cross-tool evidence. Never replay an unchanged call. "
        "Open goals (data only): "
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def goal_loop_summary(ctx) -> dict[str, Any]:
    goals = [dict(item) for item in ctx.extras.get("recovery_goals") or [] if isinstance(item, dict)]
    counts = {"pending": 0, "passed": 0, "blocked": 0}
    for goal in goals:
        raw_status = str(goal.get("status") or "pending")
        status = raw_status if raw_status in {"passed", "blocked"} else "pending"
        counts[status] = counts.get(status, 0) + 1
    return {
        "status": "blocked" if counts.get("blocked") else "pending" if counts.get("pending") else "passed" if goals else "not_required",
        "counts": counts,
        "goals": goals,
    }


def hydrate_goal_loop(ctx, trusted_contract: dict[str, Any] | None) -> None:
    """Restore unresolved generic goals from the server-owned TaskState."""
    if not isinstance(trusted_contract, dict) or ctx.extras.get("recovery_goals"):
        return
    restored = [
        dict(item) for item in trusted_contract.get("recovery_goals") or []
        if isinstance(item, dict) and item.get("status") != "passed" and item.get("goal_id")
    ]
    if not restored:
        return
    ctx.extras["recovery_goals"] = restored
    assertions = ctx.extras.setdefault("goal_assertions", [])
    if not isinstance(assertions, list):
        assertions = []
        ctx.extras["goal_assertions"] = assertions
    for goal in restored:
        goal_id = str(goal["goal_id"])
        if goal.get("goal_type") == "tool_recovery":
            assertions.append({
                "assertion_id": f"runtime-goal:{goal_id}",
                "kind": "runtime_goal_satisfied",
                "runtime_owned_recovery": True,
                "goal_id": goal_id,
            })
        elif goal.get("evidence_kind"):
            assertions.append({
                "assertion_id": f"recovery-evidence:{goal_id}",
                "kind": "evidence_claim_satisfied",
                "runtime_owned_recovery": True,
                "goal_id": goal_id,
                "evidence_kind": str(goal.get("evidence_kind") or ""),
                "target": dict(goal.get("target") or {}),
                "fact": str(goal.get("fact") or ""),
            })


def _install_generic_goal(ctx, call: Any, observation: dict[str, Any]) -> None:
    from .recovery_strategy import DEFAULT_RECOVERY_STRATEGIES

    source_call_id = str(observation["call_id"])
    identity = {
        "source_call_id": source_call_id,
        "tool_id": observation["tool_id"],
        "action": observation["action"],
        "target": observation["target"],
    }
    goal_id = "tool-goal-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    goals = ctx.extras.setdefault("recovery_goals", [])
    if not isinstance(goals, list):
        goals = []
        ctx.extras["recovery_goals"] = goals
    existing = next((item for item in goals if isinstance(item, dict) and item.get("goal_id") == goal_id), None)
    if existing is None:
        goals.append({
            "goal_id": goal_id,
            "goal_type": "tool_recovery",
            "evidence_kind": "tool_observation",
            "target": dict(observation["target"]),
            "fact": observation["action"],
            "description": f"Resolve failed {observation['tool_id']} observation",
            "source_call_id": source_call_id,
            "source_tool_id": observation["tool_id"],
            "failure_class": observation["failure_class"],
            "strategy_candidates": DEFAULT_RECOVERY_STRATEGIES.candidates(observation["failure_class"]),
            "status": "pending",
            "attempts": 1,
            "max_attempts": 3,
            "attempt_call_ids": [source_call_id],
        })
        assertions = ctx.extras.setdefault("goal_assertions", [])
        if not isinstance(assertions, list):
            assertions = []
            ctx.extras["goal_assertions"] = assertions
        assertions.append({
            "assertion_id": f"runtime-goal:{goal_id}",
            "kind": "runtime_goal_satisfied",
            "runtime_owned_recovery": True,
            "goal_id": goal_id,
        })
        _event(ctx, "goal_installed", goal_id, source_call_id, observation["failure_class"])


def _resolve_goals_from_success(ctx, call: Any, observation: dict[str, Any]) -> None:
    goals = [item for item in ctx.extras.get("recovery_goals") or [] if isinstance(item, dict)]
    pending = [item for item in goals if item.get("goal_type") == "tool_recovery" and item.get("status") == "pending"]
    explicit = {str(item) for item in (getattr(call, "goal_ids", None) or []) if str(item)}
    matches = [goal for goal in pending if str(goal.get("goal_id")) in explicit]
    if not matches:
        compatible = [goal for goal in pending if _compatible_observation(goal, observation)]
        if len(compatible) == 1:
            matches = compatible
    for goal in matches:
        goal["status"] = "passed"
        goal["resolved_by_call_id"] = observation["call_id"]
        goal["resolved_by_tool_id"] = observation["tool_id"]
        _event(ctx, "goal_satisfied", str(goal["goal_id"]), observation["call_id"], "successful_observation")


def _mark_explicit_goals_blocked(ctx, call: Any, observation: dict[str, Any]) -> None:
    explicit = {str(item) for item in (getattr(call, "goal_ids", None) or []) if str(item)}
    if not explicit:
        return
    for goal in ctx.extras.get("recovery_goals") or []:
        if not isinstance(goal, dict) or str(goal.get("goal_id")) not in explicit or goal.get("status") != "pending":
            continue
        attempts = int(goal.get("attempts") or 1) + 1
        goal["attempts"] = attempts
        goal.setdefault("attempt_call_ids", []).append(observation["call_id"])
        if attempts >= int(goal.get("max_attempts") or 3):
            goal["status"] = "blocked"
            goal["blocked_reason"] = observation["failure_class"] or "recovery_attempts_exhausted"
            _event(ctx, "goal_blocked", str(goal["goal_id"]), observation["call_id"], str(goal["blocked_reason"]))


def _compatible_observation(goal: dict[str, Any], observation: dict[str, Any]) -> bool:
    # Cross-capability evidence must be linked explicitly with plan_goal_ids.
    # A shared workspace/device key alone is not proof that an otherwise
    # unrelated successful read closed this goal.  The implicit fallback is
    # deliberately limited to one unambiguous retry of the same capability.
    return (
        str(goal.get("source_tool_id") or "") == observation["tool_id"]
        and str(goal.get("fact") or "") == observation["action"]
    )


def _should_install_goal(call: Any, observation: dict[str, Any], output: dict[str, Any]) -> bool:
    if not observation["read_only"] or str(getattr(call, "failure_policy", "replan") or "replan") != "replan":
        return False
    if bool(output.get("execution_may_continue")):
        return False
    return observation["error_code"] not in _TERMINAL_FAILURE_CODES and observation["failure_class"] not in {"policy", "authorization", "cancelled", "unknown_outcome"}


def _has_domain_recovery(output: dict[str, Any]) -> bool:
    published = output.get("runtime_recoveries")
    if isinstance(published, list) and any(isinstance(item, dict) for item in published):
        return True
    return isinstance(output.get("runtime_recovery"), dict)


def _target_from_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    target: dict[str, Any] = {}
    for key in _TARGET_KEYS:
        value = arguments.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            target[key] = value if isinstance(value, (int, float, bool)) else value[:_MAX_TARGET_TEXT]
    return target or {"scope": "current_turn"}


def _failure_class(error_code: str, error: str) -> str:
    code = error_code.upper()
    text = error.lower()
    if code in {"POLICY_BLOCKED", "FORBIDDEN"} or any(token in text for token in ("policy blocked", "security check failed")):
        return "policy"
    if code in {"AUTHORIZATION_FAILED", "UNAUTHORIZED", "CREDENTIAL_ACCESS"} or any(token in text for token in ("unauthorized", "permission denied", "credential")):
        return "authorization"
    if "CANCEL" in code:
        return "cancelled"
    if "UNCERTAIN" in code or "UNKNOWN_OUTCOME" in code:
        return "unknown_outcome"
    if code in {"ARGS_INVALID", "TOOL_ARGUMENT_VALIDATION_FAILED"} or any(token in code for token in ("ARG_", "MISSING_REQUIRED", "UNKNOWN_ARGUMENT")) or any(
        token in text for token in ("invalid argument", "invalid query", "unknown action", " is required")
    ):
        return "invalid_arguments"
    if any(token in code for token in ("TIMEOUT", "RATE_LIMIT", "CONNECTION", "HTTP_5")) or any(
        token in text for token in ("timeout", "timed out", "rate limit", "connection reset", "temporarily unavailable")
    ):
        return "transient"
    if any(token in code for token in ("NOT_FOUND", "UNSUPPORTED", "UNAVAILABLE", "REJECTED")) or any(
        token in text for token in ("not found", "unsupported", "unavailable", "rejected", "does not exist")
    ):
        return "capability"
    return "tool_failure"


def _event(ctx, event_type: str, goal_id: str, call_id: str, reason: str) -> None:
    events = ctx.extras.setdefault("recovery_goal_events", [])
    if not isinstance(events, list):
        events = []
        ctx.extras["recovery_goal_events"] = events
    events.append({
        "type": event_type,
        "goal_id": goal_id,
        "call_id": call_id,
        "reason": reason,
    })
    del events[:-256]
