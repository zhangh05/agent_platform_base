"""Deterministic evaluation baseline for platform regression gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    prompt: str
    required_tools: tuple[str, ...] = ()
    required_terms: tuple[str, ...] = ()


def evaluate_case(case: GoldenCase, result: dict[str, Any]) -> dict[str, Any]:
    called = set(result.get("tool_ids") or result.get("tools") or ())
    text = str(result.get("final_response") or result.get("output") or "").lower()
    missing_tools = sorted(set(case.required_tools) - called)
    missing_terms = sorted(term for term in case.required_terms if term.lower() not in text)
    passed = not missing_tools and not missing_terms and not result.get("error")
    return {"case_id": case.case_id, "passed": passed, "missing_tools": missing_tools, "missing_terms": missing_terms}


def run_cases(cases: list[GoldenCase], invoke: Callable[[GoldenCase], dict[str, Any]]) -> dict[str, Any]:
    results = [evaluate_case(case, invoke(case)) for case in cases]
    passed = sum(1 for result in results if result["passed"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed, "pass_rate": passed / len(results) if results else 1.0, "results": results}
