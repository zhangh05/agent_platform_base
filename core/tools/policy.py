# core/tools/policy.py
"""ToolPolicy — permission and safety enforcement with risk levels.

v3.9.5: command-level safety check is **destructive-only**.

Checks:
  1. tool_id exists in registry
  2. tool enabled
  3. risk_level metadata (low/medium/high); risk alone does not block calls
  4. category allowed (v0.2 expanded categories)
  5. not a removed or blocked tool_id (e.g. ssh.exec, nmap.scan)
  6. dry_run support
  7. timeout within limits
  8. arguments free of destructive command patterns → escalates to
     ``high`` + ``requires_approval`` (the approval bubble UX), does
     **not** block the call.
  9. broad char-blacklist (| && || ; ` $ > <) and sensitive-path
     substring (/etc/passwd, ../) are gone. Only the destructive
     command set in ``core.tools.dangerous_patterns`` matters.
"""

from core.tools.schemas import ToolSpec, ToolInvocation, PolicyDecision
from core.tools.dangerous_patterns import (
    scan_arguments_for_dangerous,
    is_destructive_command,
)

V02_ALLOWED_RISK_LEVELS = {"low", "medium", "high"}

# Forbidden tool_ids — blocked at policy level even if registered.
# v3.9.5: these are tool-level forbids (the tool should not exist
# in the LLM's namespace). They are independent of command-level
# danger — see ``dangerous_patterns`` for the command-level check.
V02_FORBIDDEN_TOOLS = {
    "ssh.exec", "telnet.exec", "snmp.walk", "nmap.scan", "ping.sweep",
    "command.exec", "device.exec", "config.push",
    "file.read_any", "file.write_any",
}

# Forbidden tool_id patterns — regex patterns that catch variants
# e.g. shell_exec_v2, config.push_force, ssh.exec_old
import re as _re
V02_FORBIDDEN_PATTERNS = [
    _re.compile(r"^shell[\._].*exec", _re.IGNORECASE),
    _re.compile(r"^ssh[\._].*exec", _re.IGNORECASE),
    _re.compile(r"^telnet[\._].*exec", _re.IGNORECASE),
    _re.compile(r"^snmp[\._].*(walk|set|write)", _re.IGNORECASE),
    _re.compile(r"^nmap[\._].*scan", _re.IGNORECASE),
    _re.compile(r"^ping[\._].*sweep", _re.IGNORECASE),
    _re.compile(r"^command\.exec$"),
    _re.compile(r"^command[\._]exec(?!_approved$)[_\w]*$", _re.IGNORECASE),  # P0-11: _approved suffix whitelist; bypass if renamed
    _re.compile(r"^device[\._].*exec", _re.IGNORECASE),
    _re.compile(r"^config[\._].*push", _re.IGNORECASE),
    _re.compile(r"^file[\._].*(read_any|write_any|delete_any)", _re.IGNORECASE),
    _re.compile(r"^powershell\.exec$"),
    _re.compile(r"^powershell[\._]exec(?!_approved)[_\w]*$", _re.IGNORECASE),
]

_DESTRUCTIVE_ACTIONS = {
    "delete", "remove", "purge", "destroy", "drop",
    "session_rewind", "rewind",
}


def _is_destructive_action(arguments: dict) -> bool:
    action = str((arguments or {}).get("action", "")).strip().lower()
    return action in _DESTRUCTIVE_ACTIONS

# Handlers accept arbitrary commands; allowlists removed in favor of
# command_policy in core.runtime_engine.
# removed entirely. The new model is destructive-only: anything not
# matching the dangerous-pattern set is treated as medium or low risk
# and is surfaced for prompt-level risk awareness, not blocked.
#
# v3.10: All policy decisions derived from CapabilityManifest (not ToolSpec).

import logging
_log = logging.getLogger(__name__)


# NOTE: mid-module definition — P2-16
def _warn(msg: str):
    _log.warning(msg)
from core.tools.schemas import V02_ALLOWED_CATEGORIES


def is_tool_forbidden(tool_id: str) -> bool:
    """Check if a tool_id is forbidden (exact match or regex pattern).

    Single source of truth for forbidden tool checks in ToolPolicy.
    """
    if tool_id in V02_FORBIDDEN_TOOLS:
        return True
    for pattern in V02_FORBIDDEN_PATTERNS:
        if pattern.search(tool_id):
            return True
    return False


# ── Public exports. The destructive-command implementation lives in
# core.tools.dangerous_patterns. ──
__all__ = [
    "ToolPolicy",
    "V02_ALLOWED_RISK_LEVELS",
    "V02_FORBIDDEN_TOOLS",
    "V02_FORBIDDEN_PATTERNS",
    "is_tool_forbidden",
    "is_destructive_command",
    "_check_argument_safety",
]


class ToolPolicy:
    """Stateless policy checker for Tool Runtime.

    Supports low/medium/high risk levels with approval gates.
    All checks are pure functions. No side effects, no state.
    """

    def check(self, spec: ToolSpec, invocation: ToolInvocation) -> PolicyDecision:
        """Run all policy checks. Returns PolicyDecision."""
        blocked = []
        reason_parts = []

        # ── 0. v3.10: CapabilityManifest is the single truth source ──
        # All policy decisions (risk, approval, idempotency, timeout, etc.)
        # must derive from CapabilityManifest, not ToolSpec alone.
        manifest = None
        if spec.tool_id:
            try:
                from core.tools.manifest_registry import get_manifest
                manifest = get_manifest(spec.tool_id)
            except Exception:
                pass

        # Override ToolSpec fields with manifest values (manifest is authoritative)
        if manifest:
            # If ToolSpec disagrees with manifest, log a warning
            if spec.risk_level and manifest.risk_level and spec.risk_level != manifest.risk_level:
                _warn(f"Tool {spec.tool_id}: ToolSpec risk={spec.risk_level} != manifest risk={manifest.risk_level}")
            effective_risk = manifest.risk_level or spec.risk_level or "low"
            effective_approval = manifest.requires_approval
            effective_destructive = manifest.destructive
            effective_idempotency = manifest.idempotency or "safe_to_retry"
            effective_timeout = manifest.timeout_seconds or spec.timeout_seconds or 30
        else:
            effective_risk = spec.risk_level or "low"
            effective_approval = spec.requires_approval
            effective_destructive = spec.destructive if hasattr(spec, 'destructive') else False
            effective_idempotency = "unsafe_to_retry"  # P0-12: default unsafe for unknown manifests
            effective_timeout = spec.timeout_seconds or 30

        # ── 1. Tool exists ──
        if not spec.tool_id:
            return PolicyDecision(
                allowed=False, reason="tool_not_found",
                risk_level="forbidden",
                blocked_rules=["tool_not_found"],
            )

        # ── 2. Enabled ──
        if not spec.enabled:
            blocked.append("tool_disabled")
            reason_parts.append(f"Tool '{spec.tool_id}' is disabled")

        # ── 3. Forbidden tool_id ──
        if is_tool_forbidden(spec.tool_id):
            blocked.append("forbidden_tool_id")
            reason_parts.append(f"Tool '{spec.tool_id}' is forbidden")

        # ── 4. Category check ──
        if spec.category and spec.category not in V02_ALLOWED_CATEGORIES:
            blocked.append("forbidden_category")
            reason_parts.append(f"Category '{spec.category}' not allowed in v0.2")

        # ── 5. Risk level gate ──
        if effective_risk not in V02_ALLOWED_RISK_LEVELS:
            blocked.append("risk_level_not_allowed")
            reason_parts.append(
                f"Tool '{spec.tool_id}' risk_level={effective_risk} not allowed"
            )

        # ── 6. Risk is metadata, not a call blocker ──
        # High-risk/destructive tools remain visible and callable by the LLM.
        # Safety enforcement happens on the arguments below.

        # ── 8. Dry-run support ──
        if invocation.dry_run and not spec.dry_run_supported:
            blocked.append("dry_run_not_supported")
            reason_parts.append(f"Tool '{spec.tool_id}' does not support dry_run")

        # ── 9. Timeout ──
        tier_max_timeout = 600 if effective_risk == "high" else (300 if effective_risk == "medium" else 120)
        # Manifest entries are the server-side trust boundary for tool
        # capabilities. Long-running tools declare their own timeout and remain
        # cancellable when their implementation supports it.
        manifest_timeout = int(getattr(manifest, "timeout_seconds", 0) or 0) if manifest else 0
        max_timeout = max(tier_max_timeout, manifest_timeout)
        if effective_timeout > max_timeout:
            blocked.append("timeout_too_high")
            reason_parts.append(
                f"Tool '{spec.tool_id}' timeout {effective_timeout}s > {max_timeout}s limit"
            )

        # ── 10. Argument safety check (v3.9.5: destructive-only) ──
        # v3.9.5: only destructive command patterns escalate. They bump
        # the effective risk to ``high`` and require approval via the
        # manifest. They DO NOT block the call outright — the user
        # can still see the bubble and approve if they want to run
        # the destructive command. Shell syntax characters
        # (|, &&, ||, ;, `, $, >, <), sensitive-path substrings, and
        # "rm -rf" in user text are no longer treated as unsafe
        # arguments.
        arg_risk, arg_reason = _check_argument_safety(
            invocation.arguments, spec.tool_id
        )
        if arg_risk == "high":
            # Escalate to high risk + approval. Do NOT block.
            effective_risk = "high"
            effective_approval = True
            reason_parts.append(
                f"Destructive command requires approval: {arg_reason}"
            )
        # arg_risk in {"medium", "low", "forbidden"} → no escalation.
        # Note: forbidden command-level is reserved for future use; tool-
        # level forbids are handled separately in step 3 above.

        # ── Decision ──
        if _is_destructive_action(invocation.arguments):
            effective_risk = "high"
            effective_approval = True
            reason_parts.append(
                f"Destructive action requires approval: {invocation.arguments.get('action')}"
            )

        requires_approval = effective_risk in ("high", "critical") and effective_approval

        if blocked:
            return PolicyDecision(
                allowed=False,
                reason="; ".join(reason_parts),
                risk_level=effective_risk,
                blocked_rules=blocked,
                requires_approval=requires_approval,
            )

        return PolicyDecision(
            allowed=True,
            reason="ok" if not reason_parts else "; ".join(reason_parts),
            risk_level=effective_risk,
            blocked_rules=[],
            requires_approval=requires_approval,
        )


def _check_argument_safety(
    arguments: dict, tool_id: str = ""
) -> tuple[str, str]:
    """Classify the argument set into a risk level for policy purposes.

    v3.9.5: returns a ``(risk_level, reason)`` tuple. The risk level
    is one of:

    - ``"high"``  — destructive command pattern detected. The caller
      should escalate the effective risk to ``high`` and require
      approval; it must NOT block the call outright.
    - ``"medium"`` — command-bearing arguments are present but no
      destructive pattern was found. The prompt layer surfaces risk
      awareness; the call proceeds.
    - ``"low"``   — no command-bearing fields present.

    Earlier versions of this function returned a single string and
    used a brittle character blacklist. That has been removed: pipe,
    chaining, redirection, expansion, sensitive-path substrings, and
    "rm -rf" appearing in non-command text are no longer reasons to
    block. The only signal is the destructive-pattern scan from
    ``core.tools.dangerous_patterns``.
    """
    if not arguments:
        return "low", ""

    # Only treat as a "command call" if the arguments contain at least
    # one command-bearing field. Otherwise this is a regular API call
    # (e.g. workspace.file) and any dangerous string in user text is
    # not a command intent.
    has_command_field = False
    for key in arguments.keys():
        key_l = str(key).lower()
        if any(_re.search(r'\b' + _re.escape(pat) + r'\b', key_l) for pat in ("command", "cmd", "shell", "script", "exec")):
            has_command_field = True
            break

    if not has_command_field:
        return "low", ""

    # Destructive-pattern scan: this is the single source of truth.
    matched = scan_arguments_for_dangerous(arguments)
    if matched:
        return "high", (
            f"destructive command pattern detected ({matched}); "
            f"requires user approval before execution"
        )

    return "medium", "exec-class tool call (non-destructive)"


def validate_tool_id(tool_id: str) -> bool:
    """Validate tool_id naming convention: category.name"""
    if not tool_id or "." not in tool_id:
        return False
    parts = tool_id.split(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False
    import re
    return bool(re.match(r'^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$', tool_id))
