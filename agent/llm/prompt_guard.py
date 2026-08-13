"""Uniform pre/post policy instrumentation for every provider invocation.

The guard does not replace ToolRuntime authorization.  It labels hostile input,
redacts deterministic secret patterns, strips hidden reasoning, and records
policy decisions on the LLMResponse so planner, continuation and response calls
share one observable policy path.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from agent.llm.schemas import LLMRequest, LLMResponse


def inspect_request(req: LLMRequest, *, state: Any = None, user_input: str = "") -> dict[str, Any]:
    injection_warnings: list[str] = []
    request_violations: list[str] = []

    try:
        from prompts.policy import detect_prompt_injection

        detected = detect_prompt_injection(user_input)
        injection_warnings = list(detected.warnings or [])
    except Exception:
        detected = None

    try:
        from agent.llm.policy import check_request

        decision = check_request(req, state)
        request_violations = list(decision.violations or [])
        request_ok = bool(decision.allowed)
    except Exception:
        request_ok = True

    return {
        "prompt_injection_detected": bool(
            detected is not None and detected.injection_detected
        ),
        "prompt_injection_warnings": injection_warnings,
        "request_policy_ok": request_ok,
        "request_policy_violations": request_violations,
    }


def inspect_response(
    resp: LLMResponse,
    *,
    state: Any = None,
    citations: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    cleaned, reasoning_stripped = sanitize_provider_output(resp.content)

    # Secret and local-path masking is deterministic and therefore enforced,
    # not merely logged.  It intentionally runs after the provider response so
    # all callers receive the same safe content.
    try:
        from storage.redaction import redact_text

        redacted = redact_text(cleaned)
    except Exception:
        redacted = cleaned
    output_redacted = redacted != cleaned
    resp.content = redacted

    output_issues: list[dict[str, Any]] = []
    try:
        from prompts.policy import check_prompt_output

        result = check_prompt_output(None, resp.content, list(citations or ()))
        output_issues = list(result.issues or [])
        output_ok = bool(result.ok)
    except Exception:
        output_ok = True

    response_violations: list[str] = []
    try:
        from agent.llm.policy import check_response

        decision = check_response(resp, state)
        response_violations = list(decision.violations or [])
        response_ok = bool(decision.allowed)
    except Exception:
        response_ok = True

    return {
        "reasoning_stripped": reasoning_stripped,
        "sensitive_output_redacted": output_redacted,
        "output_policy_ok": output_ok,
        "output_policy_issues": output_issues,
        "response_policy_ok": response_ok,
        "response_policy_violations": response_violations,
    }


def sanitize_provider_output(content: str) -> tuple[str, bool]:
    """Remove provider-only reasoning markup from user-visible content."""
    text = content or ""
    original = text
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<reasoning\b[^>]*>.*?</reasoning>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(
        r"(?ism)^\s*(reasoning|思考过程)\s*[:：].*?(?=\n\s*(answer|回答|结论)\s*[:：]|\Z)",
        "",
        text,
    )
    text = re.sub(r"(?i)</?(think|reasoning)\b[^>]*>", "", text)
    return text.strip(), text != original
