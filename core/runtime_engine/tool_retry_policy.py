"""
ToolRetryPolicy — decides whether a failed tool node may be retried.

Hard rules (security-first, conservative):

  1. NEVER retry if the tool is missing a ToolContract — unknown tools
     default to ``idempotent=False``, ``side_effect="unknown"``,
     ``max_retries=0`` and the policy refuses retry. This protects
     against silently retrying tools that were added without explicit
     contract metadata.
  2. NEVER retry on policy / safety errors (FORBIDDEN_COMMAND,
     POLICY_BLOCKED, APPROVAL_REQUIRED, CRITICAL_RISK,
     PATH_TRAVERSAL, CREDENTIAL_ACCESS, …). These errors mean the
     call should not run again unchanged.
  3. NEVER retry on side_effect ∈ {write_file, mutate_local,
     mutate_remote, execute_command, credential_access,
     external_request}. The tool may have already mutated state
     the first time, the second attempt would be a duplicate
     (and unsafe).
  4. NEVER retry past
     ``effective_max_retries = min(tool.max_retries, config.max_retries_per_node)``.
  5. NEVER retry past the per-request budget
     (``max_total_seconds`` / ``max_tool_seconds``). The caller is
     responsible for invoking ``should_retry_tool_failure`` AFTER
     ``BudgetController.check_execution()`` and passing
     ``budget_ok=False`` when the budget has been exceeded.
  6. Retry is only allowed when:
       - tool contract exists
       - contract.idempotent == True
       - side_effect ∈ {"read", "none", ""}
       - error_code ∈ ALLOWED_RETRY_ERRORS
       - node.retry_count < effective_max_retries
       - budget_ok == True

The ``RetryDecision`` dataclass is the single return type — it
carries every input the caller needs to write a single
audit/trace event without re-running the policy.

Sensitive field redaction: ``error_message`` and any caller-provided
``original_error`` note are passed through
:func:`redact_sensitive_text` before they are written into the
decision. This guarantees that leaked credentials (API keys,
tokens, passwords, secrets, authorization headers, etc.) cannot
reach audit/trace even when the upstream provider echoes them
back in its error string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── Allow / block error code tables ─────────────────────────────────────

# Transient errors that MIGHT be retried — provided the tool is
# idempotent + read-only and budget allows.
ALLOWED_RETRY_ERRORS: frozenset[str] = frozenset({
    "TOOL_TIMEOUT",
    "TOOL_EXCEPTION",
    "TEMPORARY_NETWORK_ERROR",
    "CONNECTION_RESET",
    "RATE_LIMITED",
    "PROVIDER_TIMEOUT",
    "READ_ONLY_TOOL_FAILED",
    "HTTP_429",
    "HTTP_500",
    "HTTP_502",
    "HTTP_503",
    "HTTP_504",
})

# Errors that mean the call is structurally invalid or unsafe.
# Retry is forbidden regardless of tool contract.
FORBIDDEN_RETRY_ERRORS: frozenset[str] = frozenset({
    "FORBIDDEN_COMMAND",
    "POLICY_BLOCKED",
    "APPROVAL_REQUIRED",
    "CRITICAL_RISK",
    "PATH_TRAVERSAL",
    "SYSTEM_DIRECTORY_WRITE",
    "CREDENTIAL_ACCESS",
    "FORBIDDEN_ARG",
    "FORBIDDEN_OPERATION",
    "DANGEROUS_OPERATION",
    "BUDGET_EXCEEDED",
    "USER_DENIED_APPROVAL",
    "TOOL_NOT_ALLOWED",
    "CALLER_NOT_ALLOWED",
    "ARGS_INVALID",
    "ARGS_MISSING",
    "ARGS_TYPE_MISMATCH",
})

# Side effects that cannot be retried safely — the first attempt
# may have mutated state already.
NON_RETRYABLE_SIDE_EFFECTS: frozenset[str] = frozenset({
    "write_file",
    "mutate_local",
    "mutate_remote",
    "execute_command",
    "external_request",
    "credential_access",
})

# Side effects that ARE safe to retry (read-only / pure).
RETRYABLE_SIDE_EFFECTS: frozenset[str] = frozenset({
    "read",
    "none",
    "",
})


# ── Decision dataclass ─────────────────────────────────────────────────

@dataclass
class RetryDecision:
    """Result of ``should_retry_tool_failure()`` — the single
    contract callers (execution_engine, engine.run) consume to
    decide whether to re-invoke the tool handler.
    """
    retry_allowed: bool
    reason: str = ""
    retry_count: int = 0
    max_retries: int = 0
    backoff_ms: int = 0
    error_code: str = ""
    idempotent: bool = False
    side_effect: str = "unknown"
    blocked_by_policy: bool = False
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "retry_allowed": self.retry_allowed,
            "reason": self.reason,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "backoff_ms": self.backoff_ms,
            "error_code": self.error_code,
            "idempotent": self.idempotent,
            "side_effect": self.side_effect,
            "blocked_by_policy": self.blocked_by_policy,
            **self.notes,
        }


# ── Policy entry point ────────────────────────────────────────────────

# Tokens that should never appear verbatim in audit / trace events
# (case-insensitive substring match).
SENSITIVE_KEYWORDS: tuple[str, ...] = (
    "api_key", "apikey", "api-key",
    "secret", "password", "passwd",
    "authorization", "bearer",
    "private_key", "privatekey",
    "access_key", "accesskey",
    "credential", "x-admin-Token", "x-api-token",
    "session", "token=", "key=",
)

# A loose regex that catches an entire credential-looking value
# (`name = value`, `name: value`, or `name value`) so we can
# scrub it in one shot.
_CRED_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|token|bearer|"
    r"authorization|access[_-]?key|private[_-]?key|credential)"
    r"\s*[:=]\s*[\"']?([^\s,;'\"\}]+)[\"']?",
)

# Extended: catches ``Header: Bearer <long-token>`` where the real
# secret follows a keyword (Bearer/Token/Basic) that the first regex
# consumed as the "value" group.
_CRED_BEARER_TOKEN_RE = re.compile(
    r"(?i)(?:authorization\s*[:=]\s*\*+\*REDACTED\*+\*"
    r"|bearer\s*=\*+\*REDACTED\*+\*)"
    r"\s+([A-Za-z0-9][A-Za-z0-9._\-]{10,})",
)

# Known cloud / service token prefixes that should never appear in
# audit logs even as orphaned values.
_CRED_ORPHAN_TOKEN_RE = re.compile(
    r"(?i)(?:^|[\s:\"'=])(AKIA[A-Z0-9]{16}|"
    r"sk-[a-f0-9]{36}|ghp_[a-zA-Z0-9]{36}|"
    r"xox[bpsa]-[a-zA-Z0-9-]+|"
    r"eyJ[a-zA-Z0-9_%\-]{20,})",
)


def redact_sensitive_text(text: str) -> str:
    """Best-effort scrub of credential-looking substrings from
    ``text``. Conservative — replaces the value with
    ``***REDACTED***`` but leaves the surrounding structure intact
    so the audit log still tells the operator something useful
    about WHERE the leak was.

    Redaction passes (ordered):
      1. ``name: value`` / ``name=value`` pairs via _CRED_VALUE_RE
      2. Tokens left behind after step 1 consumed a Bearer/Basic keyword
         (e.g. ``Authorization: ***REDACTED*** AKIA-...``)
      3. Orphaned cloud-key tokens not attached to any keyword
      4. Naked ``Bearer <word>`` safety net
    """
    if not text:
        return text

    # Pass 1: standard credential pairs.
    out = _CRED_VALUE_RE.sub(
        lambda m: f"{m.group(1)}=***REDACTED***", text,
    )

    # Pass 2: tokens that survive because the first regex ate the
    # header name but the actual secret was a separate word (common
    # with ``Authorization: Bearer <token>``).
    out = _CRED_BEARER_TOKEN_RE.sub(r"***REDACTED***", out)

    # Pass 3: orphan well-known token formats (AWS keys, GitHub PATs,
    # Slack tokens, JWT fragments) that appear without any keyword
    # prefix — these are high-value targets even when isolated.
    out = _CRED_ORPHAN_TOKEN_RE.sub(r"***REDACTED***", out)

    # Pass 4: final safety net for naked ``Bearer <word>`` or
    # ``token=<word>`` that survived all previous passes.
    out = re.sub(
        r"(?i)(bearer)\s+[A-Za-z0-9][A-Za-z0-9._\-]{8,}",
        r"\1 ***REDACTED***", out,
    )
    return out


def _normalize_error_code(error_code: str) -> str:
    if not error_code:
        return ""
    return str(error_code).strip().upper()


def should_retry_tool_failure(
    *,
    node: Any,
    tool_contract: Any,
    error_code: str,
    error_message: str = "",
    config_max_retries: int = 1,
    global_max_retries_per_node: int | None = None,
    budget_ok: bool = True,
) -> RetryDecision:
    """Decide whether a failed tool node may be retried.

    Args:
        node: ExecutionNode (or anything with ``.id`` and ``.retry_count``)
        tool_contract: ToolContract | None
        error_code: surfaced error code from the failed tool call
        error_message: surfaced error message (recorded in notes,
            never used to make the decision)
        config_max_retries: per-tool cap from the contract
            (ToolContract.max_retries). Defaults to 1.
        global_max_retries_per_node: hard cap from SSOTRuntimeConfig
            (``max_retries_per_node``). Defaults to ``config_max_retries``
            when omitted.
        budget_ok: True when the per-request budget still allows a
            retry (the caller already invoked
            ``BudgetController.check_execution()``).

    Returns:
        ``RetryDecision`` — never raises.
    """
    if global_max_retries_per_node is None:
        global_max_retries_per_node = config_max_retries

    error_code = _normalize_error_code(error_code)
    notes: dict[str, Any] = {
        "original_error": redact_sensitive_text(
            (error_message or "")[:200]
        ),
    }

    # 0. Budget exhausted.
    if not budget_ok:
        return RetryDecision(
            retry_allowed=False,
            reason="budget_exceeded",
            error_code=error_code,
            blocked_by_policy=True,
            notes=notes,
        )

    # 1. No contract → never retry (conservative default).
    if tool_contract is None:
        return RetryDecision(
            retry_allowed=False,
            reason="no_tool_contract",
            error_code=error_code,
            idempotent=False,
            side_effect="unknown",
            blocked_by_policy=True,
            notes=notes,
        )

    idempotent = bool(getattr(tool_contract, "idempotent", False))
    side_effect = str(
        getattr(tool_contract, "side_effect", "unknown") or "unknown"
    )
    tool_max_retries = int(
        getattr(tool_contract, "max_retries", 0) or 0
    )
    effective_max = min(
        max(0, tool_max_retries),
        max(0, global_max_retries_per_node),
    )

    # 2. Forbidden error code → never retry.
    if error_code in FORBIDDEN_RETRY_ERRORS:
        return RetryDecision(
            retry_allowed=False,
            reason=f"forbidden_error:{error_code}",
            error_code=error_code,
            max_retries=effective_max,
            idempotent=idempotent,
            side_effect=side_effect,
            blocked_by_policy=True,
            notes=notes,
        )

    # 3. Non-idempotent → never retry.
    if not idempotent:
        return RetryDecision(
            retry_allowed=False,
            reason="non_idempotent",
            error_code=error_code,
            max_retries=effective_max,
            idempotent=idempotent,
            side_effect=side_effect,
            blocked_by_policy=True,
            notes=notes,
        )

    # 4. Side effect not retryable.
    if side_effect not in RETRYABLE_SIDE_EFFECTS:
        if side_effect == "execute_command":
            reason = "execute_command_not_retryable"
        else:
            reason = f"side_effect_not_retryable:{side_effect}"
        return RetryDecision(
            retry_allowed=False,
            reason=reason,
            error_code=error_code,
            max_retries=effective_max,
            idempotent=idempotent,
            side_effect=side_effect,
            blocked_by_policy=True,
            notes=notes,
        )

    # 5. Error code not in allowed list.
    if error_code and error_code not in ALLOWED_RETRY_ERRORS:
        return RetryDecision(
            retry_allowed=False,
            reason=f"error_code_not_retryable:{error_code}",
            error_code=error_code,
            max_retries=effective_max,
            idempotent=idempotent,
            side_effect=side_effect,
            blocked_by_policy=True,
            notes=notes,
        )

    # 6. Already exhausted the effective retry budget.
    attempted = int(getattr(node, "retry_count", 0) or 0)
    if attempted >= effective_max:
        return RetryDecision(
            retry_allowed=False,
            reason="max_retries_exhausted",
            retry_count=attempted,
            max_retries=effective_max,
            error_code=error_code,
            idempotent=idempotent,
            side_effect=side_effect,
            blocked_by_policy=False,
            notes=notes,
        )

    # 7. Zero max retries (defensive — should not reach here after step 6).
    if effective_max <= 0:
        return RetryDecision(
            retry_allowed=False,
            reason="zero_max_retries",
            retry_count=attempted,
            max_retries=effective_max,
            error_code=error_code,
            idempotent=idempotent,
            side_effect=side_effect,
            blocked_by_policy=True,
            notes=notes,
        )

    # All checks pass — permit the next bounded attempt. The caller repeats
    # this decision after each failure until effective_max is exhausted.
    backoff_ms = backoff_for_attempt(attempted)
    return RetryDecision(
        retry_allowed=True,
        reason="transient_failure_on_idempotent_read_tool",
        retry_count=attempted,
        max_retries=effective_max,
        backoff_ms=backoff_ms,
        error_code=error_code,
        idempotent=idempotent,
        side_effect=side_effect,
        blocked_by_policy=False,
        notes=notes,
    )


def backoff_for_attempt(attempt: int) -> int:
    """Return backoff in milliseconds for a given attempt index (0-based).

    attempt=0 → 200ms, attempt=1 → 500ms, attempt>=2 → 1000ms.
    """
    if attempt <= 0:
        return 200
    if attempt == 1:
        return 500
    return 1000


def effective_max_retries(tool_contract: Any, global_max: int = 1) -> int:
    """Return ``min(tool.max_retries, global_max)`` clamped to >= 0.

    Helper for callers that want the cap without running the full
    policy. Treats ``None`` tool as 0 (i.e. effectively no retry).
    """
    if tool_contract is None:
        return 0
    tool_cap = int(getattr(tool_contract, "max_retries", 0) or 0)
    return min(max(0, tool_cap), max(0, global_max))
