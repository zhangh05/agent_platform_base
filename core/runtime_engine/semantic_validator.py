"""
Semantic validator for normalized QueryLoop tool calls.

Validates:
  - Tool existence in registry
  - Argument schema conformity (required, type, enum, range)
  - Path safety (in workspace only)
  - Command safety (no destructive patterns)
  - Dangerous operation marking

Returns structured validation result with risk level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import BUILTIN_CONTRACTS, get_contract, get_risk_level
from .models import ExecutionNode, RiskLevel
from .command_policy import normalize_command, evaluate_command_policy


FORBIDDEN_ARGS: list[str] = [
    "force_delete", "recursive_delete", "rm_rf",
]

DANGEROUS_IP_PATTERNS: list[str] = [
    r"^0\.0\.0\.0$",
    r"^255\.255\.255\.255$",
    r"^127\.0\.0\.1$",
]


def _has_argument_value(arguments: dict[str, Any], field_name: str) -> bool:
    if field_name not in arguments or arguments[field_name] is None:
        return False
    value = arguments[field_name]
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True

# --- Validation result types ---

@dataclass
class SemanticError:
    node_id: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticValidationResult:
    valid: bool
    errors: list[SemanticError] = field(default_factory=list)
    warnings: list[SemanticError] = field(default_factory=list)
    risk_level: str = "low"


class SemanticValidator:
    """Validate the current QueryLoop call batch."""

    def __init__(self, tool_registry: dict[str, dict[str, Any]] | None = None):
        self._registry = tool_registry or {}
        self._contracts = BUILTIN_CONTRACTS

    @staticmethod
    def _canonical_action_set(tool_id: str) -> frozenset[str] | None:
        """Return the canonical action enum for ``tool_id`` (None if unknown).

        v3.10: pulls directly from the registered ToolContract so the
        validator stays aligned with what the model is allowed to emit.
        QueryLoop has already normalized aliases before this point, so the
        set is intentional and the check is exact.
        """
        contract = get_contract(tool_id)
        if contract is None:
            return None
        schema = contract.input_schema or {}
        properties = schema.get("properties") or {}
        action = properties.get("action") or {}
        enum = action.get("enum")
        if not isinstance(enum, list) or not enum:
            return None
        return frozenset(str(x) for x in enum)

    def validate(self, nodes: list[ExecutionNode]) -> SemanticValidationResult:
        result = SemanticValidationResult(valid=True)

        for node in nodes:
            self._validate_node(node, result)

        # Compute risk level
        result.risk_level = self._compute_risk_level(nodes, result)
        result.valid = len(result.errors) == 0

        return result

    def _validate_node(
        self,
        node: ExecutionNode,
        result: SemanticValidationResult,
    ) -> None:
        # A. Tool existence
        if node.tool not in self._contracts and node.tool not in self._registry:
            result.errors.append(SemanticError(
                node_id=node.id,
                code="TOOL_NOT_FOUND",
                message=f"Tool '{node.tool}' not found in registry or contracts",
            ))
            return

        contract = get_contract(node.tool)

        # B. Argument schema
        self._validate_args(node, contract, result)
        self._validate_action_specific_required_args(node, result)

        # C. Path safety
        self._validate_path_safety(node, result)

        # D. Command safety
        self._validate_command_safety(node, result)

        # E. Dangerous operation
        if contract and contract.side_effect in ("execute_command", "credential_access"):
            result.warnings.append(SemanticError(
                node_id=node.id,
                code="DANGEROUS_OPERATION",
                message=f"Node '{node.id}' ({node.tool}) performs '{contract.side_effect}' — risk review required",
            ))

    def _validate_args(
        self,
        node: ExecutionNode,
        contract,
        result: SemanticValidationResult,
    ) -> None:
        if contract is None:
            return
        schema = contract.input_schema
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for field_name in required:
            if field_name not in node.args or node.args[field_name] is None:
                result.errors.append(SemanticError(
                    node_id=node.id,
                    code="MISSING_REQUIRED_ARG",
                    message=f"Node '{node.id}' missing required arg '{field_name}'",
                ))

        for field_name, value in node.args.items():
            if field_name not in properties:
                continue
            field_schema = properties[field_name]
            expected_type = field_schema.get("type")

            type_mismatch = False
            if expected_type == "string" and not isinstance(value, str):
                type_mismatch = True
            elif expected_type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
                type_mismatch = True
            elif expected_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                type_mismatch = True
            elif expected_type == "boolean" and not isinstance(value, bool):
                type_mismatch = True
            elif expected_type == "array" and not isinstance(value, list):
                type_mismatch = True
            elif expected_type == "object" and not isinstance(value, dict):
                type_mismatch = True

            if type_mismatch:
                result.errors.append(SemanticError(
                    node_id=node.id,
                    code="ARG_TYPE_MISMATCH",
                    message=f"Node '{node.id}' arg '{field_name}' expected {expected_type}, got {type(value).__name__}",
                ))

            if not type_mismatch and isinstance(value, (int, float)) and not isinstance(value, bool):
                minimum = field_schema.get("minimum")
                maximum = field_schema.get("maximum")
                if minimum is not None and value < minimum:
                    result.errors.append(SemanticError(
                        node_id=node.id, code="ARG_RANGE_INVALID",
                        message=f"Node '{node.id}' arg '{field_name}' must be >= {minimum}",
                    ))
                if maximum is not None and value > maximum:
                    result.errors.append(SemanticError(
                        node_id=node.id, code="ARG_RANGE_INVALID",
                        message=f"Node '{node.id}' arg '{field_name}' must be <= {maximum}",
                    ))

            if not type_mismatch and isinstance(value, list):
                item_type = (field_schema.get("items") or {}).get("type")
                if item_type == "string" and any(not isinstance(item, str) for item in value):
                    result.errors.append(SemanticError(
                        node_id=node.id, code="ARG_TYPE_MISMATCH",
                        message=f"Node '{node.id}' arg '{field_name}' items must be string",
                    ))

            # Enum validation is strictly canonical. QueryLoop normally
            # normalizes known aliases before this guard runs.
            enum_values = field_schema.get("enum")
            if enum_values and value not in enum_values:
                # Defense in depth: also reject if value is in the
                # alias table but not normalized. This means the
                # normalization layer was bypassed (a future bug);
                # we want a clear error rather than silent acceptance.
                from .action_alias import resolve_action_alias
                resolution = resolve_action_alias(node.tool, str(value))
                if resolution.matched:
                    result.errors.append(SemanticError(
                        node_id=node.id,
                        code="ACTION_ALIAS_NOT_NORMALIZED",
                        message=(
                            f"Node '{node.id}' arg '{field_name}' value '{value}' is a known alias "
                            f"(→ '{resolution.canonical_action}') but was not normalized by QueryLoop."
                        ),
                    ))
                else:
                    result.errors.append(SemanticError(
                        node_id=node.id,
                        code="ARG_ENUM_INVALID",
                        message=f"Node '{node.id}' arg '{field_name}' value '{value}' not in allowed enum: {enum_values}",
                    ))

        # Forbidden args
        for forbidden in FORBIDDEN_ARGS:
            if forbidden in node.args and node.args[forbidden]:
                result.errors.append(SemanticError(
                    node_id=node.id,
                    code="FORBIDDEN_ARG",
                    message=f"Node '{node.id}' uses forbidden arg '{forbidden}'",
                ))

    def _validate_action_specific_required_args(
        self,
        node: ExecutionNode,
        result: SemanticValidationResult,
    ) -> None:
        """Validate conditional requirements for every merged tool action."""
        from core.tools.action_requirements import ACTION_REQUIRED_ALL, ACTION_REQUIRED_ANY

        action = str(node.args.get("action") or "shell").strip().lower()
        key = (node.tool, action)

        for field_name in ACTION_REQUIRED_ALL.get(key, ()):
            if not _has_argument_value(node.args, field_name):
                result.errors.append(SemanticError(
                    node_id=node.id,
                    code="MISSING_REQUIRED_ARG",
                    message=f"Node '{node.id}' missing required arg '{field_name}' for {node.tool} action={action}",
                ))

        for alternatives in ACTION_REQUIRED_ANY.get(key, ()):
            if not any(_has_argument_value(node.args, field_name) for field_name in alternatives):
                result.errors.append(SemanticError(
                    node_id=node.id,
                    code="MISSING_REQUIRED_ARG",
                    message=(
                        f"Node '{node.id}' requires one of {list(alternatives)} "
                        f"for {node.tool} action={action}"
                    ),
                ))

    def _validate_path_safety(
        self,
        node: ExecutionNode,
        result: SemanticValidationResult,
    ) -> None:
        """Ensure file paths are within workspace boundaries."""
        path = node.args.get("path", "")
        if not path or not isinstance(path, str):
            return

        dangerous_prefixes = ["/etc/", "/System/", "/boot/", "C:\\Windows\\", "C:\\WINDOWS\\",
                              "/var/run/", "/dev/", "/proc/", "/sys/"]
        for prefix in dangerous_prefixes:
            if path.startswith(prefix):
                result.warnings.append(SemanticError(
                    node_id=node.id,
                    code="DANGEROUS_PATH",
                    message=f"Node '{node.id}' accesses system path '{path}'",
                ))

    def _validate_command_safety(
        self,
        node: ExecutionNode,
        result: SemanticValidationResult,
    ) -> None:
        """Check for forbidden command patterns using command_policy (v1.0 unified).
        v3.12: destructive commands (rm -f, rm -rf, git reset --hard, etc.)
        are NOT blocked here — they are routed to the RiskPolicyEngine's
        approval gate instead.
        """
        command = node.args.get("command", "")
        if not command or not isinstance(command, str):
            return

        # v3.12: check destructive patterns before command_policy.
        # Commands that match our destructive patterns are deferred
        # to the RiskPolicyEngine (approval_required), not blocked.
        if _is_destructive_for_approval(command):
            return

        normalized = normalize_command(command)
        decision = evaluate_command_policy(normalized)

        if not decision.allowed:
            result.errors.append(SemanticError(
                node_id=node.id,
                code=decision.error_code or "FORBIDDEN_COMMAND",
                message=decision.reason or f"Node '{node.id}' command blocked by policy",
                details=decision.to_dict(),
            ))

    def _compute_risk_level(
        self,
        nodes: list[ExecutionNode],
        result: SemanticValidationResult,
    ) -> str:
        """Compute composite risk level from nodes and errors."""
        max_risk = RiskLevel.LOW

        for node in nodes:
            node_risk = get_risk_level(node.tool)
            try:
                rl = RiskLevel(node_risk)
            except ValueError:
                rl = RiskLevel.LOW
            if rl.value == "critical" or rl.value == "high":
                if rl == RiskLevel.CRITICAL and max_risk != RiskLevel.CRITICAL:
                    max_risk = rl
                elif rl == RiskLevel.HIGH and max_risk not in (RiskLevel.CRITICAL, RiskLevel.HIGH):
                    max_risk = rl

        # Combo escalation
        write_count = sum(1 for n in nodes if get_contract(n.tool) and get_contract(n.tool).side_effect in ("write_file", "mutate_local"))
        exec_count = sum(1 for n in nodes if get_contract(n.tool) and get_contract(n.tool).side_effect == "execute_command")

        if write_count >= 3 and max_risk == RiskLevel.MEDIUM:
            max_risk = RiskLevel.HIGH
        if exec_count >= 2 and max_risk != RiskLevel.CRITICAL:
            max_risk = RiskLevel.HIGH
        if exec_count >= 3:
            max_risk = RiskLevel.CRITICAL

        return max_risk.value


# ── v3.12: Destructive command patterns shared with risk_policy ────────
# Commands matching these patterns are NOT blocked by semantic validation.
# Instead, they are deferred to RiskPolicyEngine for approval_required
# (or hard_block for system-destroy patterns).

_SV_DESTRUCTIVE_RE = r"(?i)(^|\s)(rm\s+-[rf]|del\s+/[fs]|rmdir\s+/s|Remove-Item\s+-Recurse|git\s+reset\s+--hard|git\s+clean\s+-fd|docker\s+system\s+prune|kubectl\s+delete|chmod\s+-R\s+777|chown\s+-R|dd\s+if=)"


def _is_destructive_for_approval(command: str) -> bool:
    """Check if a command is destructive-but-approvable (not system-destroy).

    Returns True if the command should skip command_policy blocking
    and be deferred to RiskPolicyEngine's approval gate.
    """
    import re
    return bool(re.search(_SV_DESTRUCTIVE_RE, command))
