"""
Risk Policy Engine for SSOT Runtime Engine.

Assesses the current QueryLoop tool-call batch and distinguishes between:
  - **allow**: safe to run directly
  - **approval_required**: needs user confirmation (frontend approval bubble)
  - **hard_block**: absolutely forbidden, cannot be overridden

This is the last gate before execution — hard_block rejects immediately;
approval_required defers to the caller for user consent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import BUILTIN_CONTRACTS, get_contract, get_risk_level
from .models import ExecutionNode, RiskLevel
from .command_policy import normalize_command, evaluate_command_policy


# ── Destructive command patterns (trigger approval, not hard block) ────

_DESTRUCTIVE_COMMAND_PATTERNS: list[tuple[str, str]] = [
    # Each tuple: (regex, human_label)
    (r"(^|\s)rm\s+-f\b", "rm -f"),
    (r"(^|\s)rm\s+-rf\b", "rm -rf"),
    (r"(^|\s)del\s+/f\b", "del /f"),
    (r"(^|\s)rmdir\s+/s\b", "rmdir /s"),
    (r"(?i)remove-item\s+-recurse", "Remove-Item -Recurse"),
    (r"(^|\s)format\b", "format"),
    (r"(^|\s)mkfs\b", "mkfs"),
    (r"(^|\s)dd\s+if=", "dd if="),
    (r"chmod\s+-R\s+777", "chmod -R 777"),
    (r"chown\s+-R\b", "chown -R"),
    (r"git\s+reset\s+--hard", "git reset --hard"),
    (r"git\s+clean\s+-fd", "git clean -fd"),
    (r"docker\s+system\s+prune", "docker system prune"),
    (r"kubectl\s+delete\b", "kubectl delete"),
    (r"(^|\s)delete\b", "delete"),
    (r"drop\s+database\b", "drop database"),
    (r"truncate\s+table\b", "truncate table"),
]


# ── Hard-block patterns (absolute, no approval possible) ────────────

_SYSTEM_DESTROY_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf\s+/(\s|$)", "rm -rf /"),
    (r"rm\s+-rf\s+/\*", "rm -rf /*"),
    # Windows paths: match both \ and / separators after normalization
    (r"del\s+C:[\\/]Windows", "del C:\\Windows"),
    (r"del\s+C:[\\/]Users", "del C:\\Users"),
    (r"format\s+C:", "format C:"),
]


@dataclass
class RiskAssessment:
    """Result of a tool-call batch risk check."""
    risk_level: str = "low"
    safe_to_run: bool = True
    requires_approval: bool = False
    hard_block: bool = False
    blocked_reason: str = ""
    blocked_nodes: list[str] = field(default_factory=list)
    approval_reason: str = ""
    approval_nodes: list[str] = field(default_factory=list)
    approval_details: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    combo_reasons: list[str] = field(default_factory=list)
    alias_normalizations: list[dict[str, str]] = field(default_factory=list)


class RiskPolicyEngine:
    """Risk assessment for normalized QueryLoop tool calls.

    Rules:
      - credential_access / system dir delete → **hard_block**
      - Destructive commands (rm -rf, git reset --hard, etc.) → **approval_required**
      - Large write/exec/tool batches → warnings only. They are bounded by
        runtime budgets and must not trigger approval by themselves.
    """

    def __init__(self, config=None):
        # late-import to avoid circular dependency at module level
        from .models import SSOTRuntimeConfig
        cfg = config if config is not None else SSOTRuntimeConfig()
        self._max_tool_allow = getattr(cfg, "rp_max_tool_nodes_allow", 20)
        self._max_tool_approval = getattr(cfg, "rp_max_tool_nodes_approval", 50)
        self._max_exec_allow = getattr(cfg, "rp_max_exec_allow", 5)
        self._max_exec_approval = getattr(cfg, "rp_max_exec_approval", 20)

    def assess(self, nodes: list[ExecutionNode]) -> RiskAssessment:
        assessment = RiskAssessment()

        exec_count = 0
        write_count = 0
        cred_count = 0
        external_count = 0

        for node in nodes:
            contract = get_contract(node.tool)
            if contract is None:
                continue

            node_risk = contract.risk_level

            # ── CRITICAL contract risk → hard block (e.g. credential_access) ──
            if node_risk == RiskLevel.CRITICAL.value:
                assessment.blocked_nodes.append(node.id)
                assessment.hard_block = True
                assessment.safe_to_run = False
                assessment.blocked_reason = (
                    f"Critical-risk node '{node.id}' ({node.tool}) — hard blocked"
                )

            # ── HIGH contract risk → approval gate ──
            elif node_risk == RiskLevel.HIGH.value:
                if node.id not in assessment.approval_nodes:
                    assessment.approval_nodes.append(node.id)
                assessment.requires_approval = True
                node.approval_required = True

            # ── Contract-based approval flag ──
            if contract.requires_approval:
                if node.id not in assessment.approval_nodes:
                    assessment.approval_nodes.append(node.id)
                assessment.requires_approval = True
                node.approval_required = True

            # ── Unified command policy check ──
            if node.tool == "exec.run" and "command" in node.args:
                cmd = node.args.get("command", "")
                if cmd and isinstance(cmd, str):
                    # System destroy check (hard block) — MUST run first
                    # so we hard-block before any other decision.
                    sys_dest_label = _check_system_destroy(cmd)
                    if sys_dest_label:
                        assessment.blocked_nodes.append(node.id)
                        assessment.hard_block = True
                        assessment.safe_to_run = False
                        assessment.blocked_reason = (
                            f"System-destroy command in node '{node.id}': {sys_dest_label}"
                        )
                        continue  # don't process further — already hard blocked

                    # Destructive command check (approval, not hard block).
                    # We mark it as approval_required but do NOT continue —
                    # command_policy below still runs so that credential
                    # patterns (cat ~/.ssh/id_rsa, etc.) take precedence
                    # and hard_block the node.
                    dest_label = _check_destructive_command(cmd)
                    if dest_label:
                       if node.id not in assessment.approval_nodes:
                           assessment.approval_nodes.append(node.id)
                       assessment.requires_approval = True
                       assessment.approval_reason = (
                           assessment.approval_reason or "destructive_command"
                       )
                       assessment.approval_details.append({
                           "node_id": node.id,
                           "tool": node.tool,
                           "command": cmd[:200],
                           "risk_reason": dest_label,
                       })

                    # Unified command policy check.
                    # Runs AFTER destructive check.  If command_policy
                    # blocks for destructive-only reasons (rm, rm -rf,
                    # del, rd) we downgrade to approval_required instead
                    # of hard_block.  Credential / path-traversal /
                    # registry / PowerShell-abuse blocks remain hard_block.
                    normalized = normalize_command(cmd)
                    decision = evaluate_command_policy(normalized)
                    if not decision.allowed:
                        reason_lower = (decision.reason or "").lower()
                        # Destructive-only blocks → approval, not hard_block
                        if _is_cp_destructive_only(reason_lower):
                            if node.id not in assessment.approval_nodes:
                                assessment.approval_nodes.append(node.id)
                            assessment.requires_approval = True
                            if not assessment.approval_reason:
                                assessment.approval_reason = "destructive_command"
                        else:
                            # Real hard block: credential, path traversal,
                            # registry, PowerShell abuse, etc.
                            assessment.blocked_nodes.append(node.id)
                            assessment.hard_block = True
                            assessment.safe_to_run = False
                            assessment.blocked_reason = (
                                assessment.blocked_reason or
                                f"Command policy blocked node '{node.id}': {decision.reason}"
                            )

                    # Credential scan: commands containing destructive
                    # patterns AND credential patterns are hard_blocked
                    # regardless of command_policy's result.  This catches
                    # combos like "rm -rf /tmp && cat ~/.ssh/id_rsa" where
                    # command_policy short-circuits on the destructive
                    # pattern and never reaches the credential check.
                    if dest_label and _has_credential_pattern(cmd, node.args):
                        if not assessment.hard_block:
                            assessment.blocked_nodes.append(node.id)
                            assessment.hard_block = True
                            assessment.safe_to_run = False
                            assessment.blocked_reason = (
                                assessment.blocked_reason or
                                f"Destructive+credential combo in node '{node.id}'"
                            )

            # ── Side-effect counts for combo escalation ──
            # v4.5: action-aware counting — a mixed tool (workspace.file,
            # workspace.artifact, report.manage) that supports both read and
            # write sub-actions should only increment write_count for the
            # write actions. Previously the hard-coded contract side_effect
            # counted every call as a write, creating noisy batch warnings
            # when the LLM simply read 3+ files.
            se = contract.side_effect
            action = str(node.args.get("action", "")).lower()
            is_read_action = action in ("read", "list", "glob", "read_image", "diff", "export", "references", "status", "log", "get")
            if se == "execute_command":
                exec_count += 1
            elif se in ("write_file", "mutate_local"):
                if not is_read_action:
                    write_count += 1
            elif se == "external_request":
                external_count += 1
            elif se == "credential_access":
                cred_count += 1

            # ── Alias normalization bookkeeping ──
            if node.action_normalized_from_alias and node.action_original:
                assessment.alias_normalizations.append({
                    "node_id": node.id,
                    "action_original": node.action_original,
                    "action_normalized": node.args.get("action", ""),
                })

        # ── Combo escalation ──
        self._apply_combo_escalation(
            assessment, exec_count, write_count,
            external_count, cred_count, nodes,
        )

        # ── Compute composite risk ──
        assessment.risk_level = self._compute_composite(nodes)

        # ── If hard_block is already set, nothing else matters ──
        if assessment.hard_block:
            assessment.safe_to_run = False
            return assessment

        # ── If approval is required, mark safe_to_run accordingly ──
        if assessment.requires_approval:
            # Not hard_block, but needs user consent — still
            # "not safe to run automatically"
            assessment.safe_to_run = False

        return assessment

    def _apply_combo_escalation(
        self,
        assessment: RiskAssessment,
        exec_count: int,
        write_count: int,
        external_count: int,
        cred_count: int,
        nodes: list[ExecutionNode],
    ) -> None:
        total_nodes = len(nodes)

        # 3+ writes → warning only. The user-facing policy is destructive-only
        # approval; ordinary batches stay usable and are bounded elsewhere.
        if write_count >= 3 and not assessment.hard_block:
            assessment.combo_reasons.append(f"{write_count} write/mutate operations")
            assessment.warnings.append(
                f"Combo: {write_count} write operations detected"
            )

        # exec.run tiers → warning only. QueryLoop/tool budgets cap runtime.
        if exec_count > self._max_exec_allow and not assessment.hard_block:
            assessment.combo_reasons.append(
                f"{exec_count} command executions"
            )
            assessment.warnings.append(
                f"Large command batch: {exec_count} exec nodes"
            )

        # Total node tiers → warning only. Planner and QueryLoop enforce caps.
        if total_nodes > self._max_tool_allow and not assessment.hard_block:
            assessment.warnings.append(
                f"Large tool batch: {total_nodes} total nodes"
            )

        # exec + external + credential → warning only unless a concrete
        # credential-access command was already hard-blocked above.
        if exec_count and external_count and cred_count and not assessment.hard_block:
            assessment.combo_reasons.append(
                "exec + external + credential_access combo"
            )
            assessment.warnings.append(
                "Combo: exec + external + credential context detected"
            )

    def _compute_composite(self, nodes: list[ExecutionNode]) -> str:
        max_risk = RiskLevel.LOW
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        for node in nodes:
            node_risk = get_risk_level(node.tool)
            try:
                rl = RiskLevel(node_risk)
            except ValueError:
                continue
            if risk_order.get(rl.value, 0) > risk_order.get(max_risk.value, 0):
                max_risk = rl
        return max_risk.value


def _check_destructive_command(cmd: str) -> str:
    """Return a human-readable label if ``cmd`` matches a destructive
    pattern that should trigger an approval gate (NOT hard block)."""
    # P3-5: consider precompiling union regex instead of list traversal
    for pattern, label in _DESTRUCTIVE_COMMAND_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return label
    return ""


def _check_system_destroy(cmd: str) -> str:
    """Return a human-readable label if ``cmd`` matches a system-destroy
    pattern that should be hard-blocked. None if safe from that perspective."""
    cmd_norm = cmd.replace("\\", "/")
    for pattern, label in _SYSTEM_DESTROY_PATTERNS:  # P3-5: same precompile concern
        # Use re.IGNORECASE instead of .lower() on the pattern —
        # .replace('\\','/') would destroy regex metacharacters like \s.
        if re.search(pattern, cmd_norm, re.IGNORECASE):
            return label
    return ""


# ── Command-policy destructive-only block detection ────────────────────

# Patterns that command_policy blocks for destructive-only reasons.
# When command_policy returns not_allowed with one of these reasons,
# we downgrade from hard_block to approval_required because the
# command is destructive-but-approvable (not a true security threat).

_CP_DESTRUCTIVE_ONLY_PATTERNS: list[str] = [
    "destructive command pattern",
    "powershell cmdlet 'rm'",
    "powershell cmdlet 'remove-item'",
    "powershell cmdlet 'rmdir'",
    "powershell cmdlet 'del'",
]


def _is_cp_destructive_only(reason: str) -> bool:
    """Returns True if command_policy blocked for destructive-only reasons.

    These are downgraded from hard_block to approval_required by
    the risk policy.  Credential access, path traversal, registry
    abuse, and PowerShell injection NEVER match here.
    """
    reason_lower = reason.lower()
    for pat in _CP_DESTRUCTIVE_ONLY_PATTERNS:
        if pat in reason_lower:
            return True
    return False


_CREDENTIAL_SCAN_RE = re.compile(
    r"(?i)(~/.ssh/id_|private[_-]?key|\.pem\b|-----BEGIN|secret|password|token|api[_-]?key|authorization|bearer|credential)",
)


def _has_credential_pattern(cmd: str, node_args: dict = None) -> bool:
    """Quick scan for credential/private-key patterns in command and related fields."""
    fields_to_scan = [cmd]
    if node_args:
        for field in ("script", "script_body", "args", "env", "password", "secret"):
            val = node_args.get(field, "")
            if isinstance(val, str) and val:
                fields_to_scan.append(val)
    return bool(_CREDENTIAL_SCAN_RE.search("|".join(fields_to_scan)))
