# agent/llm/policy.py
"""LLM Policy Gate — request and response validation."""

from agent.llm.schemas import (
    LLMRequest, LLMResponse, PolicyDecision,
    ALLOWED_TASKS, BLOCKED_TASKS, LLMTask,
)

SECRET_PATTERNS = ["password", "secret", "token", "api_key", "private_key", "credential", "key_string"]


def check_request(req: LLMRequest, state=None) -> PolicyDecision:
    """Check LLM request before sending to provider."""
    violations = []

    # Task must be in allowed set
    if req.task in BLOCKED_TASKS:
        violations.append(f"blocked task: {req.task}")
    elif req.task not in ALLOWED_TASKS:
        violations.append(f"unknown task: {req.task}")

    # Safe context must not contain oversized raw payloads or secrets.
    ctx = req.safe_context or {}
    ctx_str = str(ctx).lower()

    for key, value in ctx.items():
        if isinstance(value, str) and len(value) > 8000:
            violations.append(f"safe_context contains oversized field: {key}")

    for secret in SECRET_PATTERNS:
        if secret in ctx_str:
            violations.append(f"safe_context may contain {secret}")

    if violations:
        return PolicyDecision(allowed=False, reason="; ".join(violations), violations=violations)

    return PolicyDecision(allowed=True, reason="request_policy_pass")


def check_response(resp: LLMResponse, state=None) -> PolicyDecision:
    """Check LLM response for safety violations."""
    violations = []
    content = (resp.content or "").lower()

    # Must not claim real-world execution without evidence.
    unsafe_claims = [
        ("已执行", "claims execution completed"),
        ("已修改生产环境", "claims production mutation completed"),
        ("production-ready", "claims production readiness"),
        ("manual review passed", "claims manual_review passed"),
        ("no issues found", "claims no issues found"),
    ]
    for kw, reason in unsafe_claims:
        idx = content.find(kw)
        if idx >= 0:
            # Check negation context — LLM may say it will not claim execution.
            try:
                from prompts.policy import is_negation_context
            except ImportError:
                # Fallback: simple negation check if prompt policy unavailable
                def is_negation_context(text, idx, window=80):
                    snippet = text[max(0, idx-window):idx]
                    return any(neg in snippet.lower() for neg in ("不会", "不", "cannot", "won't", "never"))
            if not is_negation_context(resp.content, idx):
                violations.append(reason)

    # Must not leak secrets — pattern-based detection
    for secret in SECRET_PATTERNS:
        # Flag only when secret appears with surrounding whitespace or 
        # key=value-like patterns suggesting intentional secret exposure
        word_boundary_hits = content.count(f" {secret} ") + content.count(f"\n{secret} ")
        if word_boundary_hits > 0 and content.count(secret) < 3:
            violations.append(f"potential {secret} leak in response")

    # Must not fake planned module results
    if "generated report" in content or "analysis report generated" in content:
        violations.append("may be faking planned module result")

    # Must not claim LLM modified config
    if "i modified" in content or "i changed" in content or "i updated" in content:
        if "production-ready" in content:
            violations.append("LLM claims to have modified config")

    if violations:
        return PolicyDecision(allowed=False, reason="; ".join(violations), violations=violations)

    return PolicyDecision(allowed=True, reason="response_policy_pass")
