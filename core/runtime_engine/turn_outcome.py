"""Pure turn outcome projection shared by the QueryLoop exit paths."""
from __future__ import annotations

from typing import Any


def derive_tool_execution_outcome(tool_results: list[Any]) -> str:
    """Aggregate tool-attempt facts without claiming the user task outcome."""
    if any(
        bool(getattr(result, "execution_may_continue", False))
        and not bool((getattr(result, "output", None) or {}).get("read_only", False))
        for result in tool_results
    ):
        return "unknown"
    coverage_states = [
        str((getattr(result, "output", None) or {}).get("coverage_status") or "").lower()
        for result in tool_results
        if isinstance(getattr(result, "output", None), dict)
    ]
    if "failed" in coverage_states and not ({"complete", "partial"} & set(coverage_states)):
        return "failed"
    partial_success = any(
        bool((getattr(result, "output", None) or {}).get("partial"))
        or str((getattr(result, "output", None) or {}).get("coverage_status") or "").lower() == "partial"
        for result in tool_results
        if isinstance(getattr(result, "output", None), dict)
    )
    successful = sum(1 for result in tool_results if bool(getattr(result, "ok", False)))
    failed = len(tool_results) - successful
    if partial_success or (successful and failed):
        return "partial"
    if failed:
        return "failed"
    return "complete"


def derive_execution_outcome(
    tool_results: list[Any],
    *,
    terminal_error: str | None = None,
    goal_assertions: dict[str, Any] | None = None,
) -> str:
    """Project whether the user task completed, independently of failed attempts.

    A recovered tool failure is process telemetry, not a partial user outcome. A
    turn is complete when the loop reaches a valid final response with at least
    one successful operation (or no operation was needed). Explicit terminal
    errors and required goal assertions remain authoritative blockers.
    """
    tool_outcome = derive_tool_execution_outcome(tool_results)
    if tool_outcome == "unknown":
        return "unknown"
    assertions = goal_assertions if isinstance(goal_assertions, dict) else {}
    if assertions.get("required") and assertions.get("status") != "passed":
        return "unknown" if assertions.get("status") == "unknown" else "partial"

    successful = sum(1 for result in tool_results if bool(getattr(result, "ok", False)))
    if terminal_error:
        return "partial" if successful else "failed"
    if tool_results and successful == 0:
        return "failed"
    return "complete"
