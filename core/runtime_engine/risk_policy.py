"""
Risk Policy Engine for SSOT Runtime Engine.

Assesses the current QueryLoop tool-call batch and distinguishes between
allowed calls and absolute policy blocks. Product-specific authorization is
enforced by the owning tool handler, such as a selected Skill's device and
connection boundary for network configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import BUILTIN_CONTRACTS, get_contract, get_risk_level
from .models import ExecutionNode, RiskLevel
from .command_policy import normalize_command, evaluate_command_policy


# ── Destructive command patterns (hard blocked) ──────────────────────

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


# ── Absolute hard-block patterns ───────────────────────────────────

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
    hard_block: bool = False
    blocked_reason: str = ""
    blocked_nodes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    combo_reasons: list[str] = field(default_factory=list)
    alias_normalizations: list[dict[str, str]] = field(default_factory=list)


class RiskPolicyEngine:
    """Risk assessment for normalized QueryLoop tool calls.

    Rules:
      - credential_access / system dir delete → **hard_block**
      - Destructive shell commands (rm -rf, git reset --hard, etc.) → **hard_block**
      - Large write/exec/tool batches → warnings only. They are bounded by
        runtime budgets and remain warnings rather than execution blocks.
    """

    def __init__(self, config=None):
        # late-import to avoid circular dependency at module level
        from .models import SSOTRuntimeConfig
        cfg = config if config is not None else SSOTRuntimeConfig()
        self._max_tool_allow = getattr(cfg, "rp_max_tool_nodes_allow", 20)
        self._max_exec_allow = getattr(cfg, "rp_max_exec_allow", 5)

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

            action = str(node.args.get("action") or "").lower()
            action_contract = dict((contract.action_contracts or {}).get(action) or {})
            node_risk = str(action_contract.get("risk_level") or contract.risk_level)

            # ── CRITICAL contract risk → hard block (e.g. credential_access) ──
            if node_risk == RiskLevel.CRITICAL.value:
                assessment.blocked_nodes.append(node.id)
                assessment.hard_block = True
                assessment.safe_to_run = False
                assessment.blocked_reason = (
                    f"Critical-risk node '{node.id}' ({node.tool}) — hard blocked"
                )

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

                    # Destructive shell commands are not recoverable through a
                    # product Skill contract, so reject them at the canonical gate.
                    dest_label = _check_destructive_command(cmd)
                    if dest_label:
                        assessment.blocked_nodes.append(node.id)
                        assessment.hard_block = True
                        assessment.safe_to_run = False
                        assessment.blocked_reason = (
                            assessment.blocked_reason or
                            f"Destructive command in node '{node.id}': {dest_label}"
                        )

                    # Unified command policy check.
                    # Runs AFTER destructive check.  If command_policy
                    # Command-policy violations are hard blocks. Product
                    # authorization cannot override host command safety.
                    normalized = normalize_command(cmd)
                    decision = evaluate_command_policy(normalized)
                    if not decision.allowed:
                        reason_lower = (decision.reason or "").lower()
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
            action = str(node.args.get("action", "")).lower()
            action_contract = dict((contract.action_contracts or {}).get(action) or {})
            se = str(action_contract.get("side_effects") or contract.side_effect)
            action_class = str(action_contract.get("action_class") or "")
            is_read_action = bool(action_contract.get("read_only")) or action in (
                "read", "list", "glob", "read_image", "diff", "export", "references", "status", "log", "get",
            )
            if action_class == "execute" or se == "execute_command":
                exec_count += 1
            elif action_class in {"write", "delete"} or se in ("write_file", "mutate_local"):
                if not is_read_action:
                    write_count += 1
            elif action_class == "network" or se in {"external_request", "external_read"}:
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
        # warning; ordinary batches stay usable and are bounded elsewhere.
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
            contract = get_contract(node.tool)
            action = str(node.args.get("action") or "").lower()
            action_contract = dict((contract.action_contracts or {}).get(action) or {}) if contract else {}
            node_risk = str(action_contract.get("risk_level") or get_risk_level(node.tool))
            try:
                rl = RiskLevel(node_risk)
            except ValueError:
                continue
            if risk_order.get(rl.value, 0) > risk_order.get(max_risk.value, 0):
                max_risk = rl
        return max_risk.value


def _check_destructive_command(cmd: str) -> str:
    """Return a human-readable label for a blocked destructive command."""
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
