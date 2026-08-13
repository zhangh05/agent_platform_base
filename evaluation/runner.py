"""Deterministic evaluation baseline for platform regression gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    prompt: str
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    required_terms: tuple[str, ...] = ()
    accepted_statuses: tuple[str, ...] = ()
    max_tool_calls: int | None = None
    require_evidence: bool = False


def evaluate_case(case: GoldenCase, result: dict[str, Any]) -> dict[str, Any]:
    called_list = list(result.get("tool_ids") or result.get("tools") or ())
    called = set(called_list)
    text = str(result.get("final_response") or result.get("output") or "").lower()
    missing_tools = sorted(set(case.required_tools) - called)
    forbidden_tools = sorted(set(case.forbidden_tools) & called)
    missing_terms = sorted(term for term in case.required_terms if term.lower() not in text)
    status = str(result.get("status") or ("failed" if result.get("error") else "complete"))
    status_ok = not case.accepted_statuses or status in case.accepted_statuses
    tool_count_ok = case.max_tool_calls is None or len(called_list) <= case.max_tool_calls
    evidence_count = int(result.get("evidence_count") or len(result.get("evidence_parts") or ()))
    evidence_ok = not case.require_evidence or evidence_count > 0
    passed = all((
        not missing_tools,
        not forbidden_tools,
        not missing_terms,
        not result.get("error"),
        status_ok,
        tool_count_ok,
        evidence_ok,
    ))
    return {
        "case_id": case.case_id,
        "passed": passed,
        "missing_tools": missing_tools,
        "forbidden_tools": forbidden_tools,
        "missing_terms": missing_terms,
        "status": status,
        "status_ok": status_ok,
        "tool_call_count": len(called_list),
        "tool_count_ok": tool_count_ok,
        "evidence_count": evidence_count,
        "evidence_ok": evidence_ok,
    }


def run_cases(cases: list[GoldenCase], invoke: Callable[[GoldenCase], dict[str, Any]]) -> dict[str, Any]:
    results = [evaluate_case(case, invoke(case)) for case in cases]
    passed = sum(1 for result in results if result["passed"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed, "pass_rate": passed / len(results) if results else 1.0, "results": results}
