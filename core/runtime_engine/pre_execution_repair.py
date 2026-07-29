"""
Pre-Execution Repair Engine for SSOT Runtime.

Handles recoverable semantic validation errors BEFORE execution fails.

Repairable errors:
  - ARG_ENUM_INVALID        → action alias normalization
  - MISSING_REQUIRED_ARG    → return to the LLM for correction
  - PLAN_SCHEMA_INVALID     → deterministic patch
  - INVALID_ACTION_ALIAS    → normalize action/operation
  - NODE_ARG_NORMALIZABLE   → general arg fixing

Non-repairable (security/policy):
  - FORBIDDEN_COMMAND
  - POLICY_BLOCKED
  - APPROVAL_REQUIRED
  - CRITICAL_RISK
  - PATH_TRAVERSAL
  - SYSTEM_DIRECTORY_WRITE
  - CREDENTIAL_ACCESS
  - BUDGET_EXCEEDED

Strategy:
  1. Deterministic repair (no LLM) — 1 attempt max
  2. LLM-based planner repair — 1 attempt max, controlled by budget

Trace + Audit: every repair attempt is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# Repair-eligible error codes
# ============================================================================

REPAIRABLE_ERROR_CODES: set[str] = {
    "ARG_ENUM_INVALID",
    "ACTION_ALIAS_NOT_NORMALIZED",
    "MISSING_REQUIRED_ARG",
    "ARG_TYPE_MISMATCH",
    "INVALID_ACTION_ALIAS",
    "NODE_ARG_NORMALIZABLE",
    "POTENTIAL_MISSING_INPUT",
}

NON_REPAIRABLE_ERROR_CODES: set[str] = {
    "FORBIDDEN_COMMAND",
    "POLICY_BLOCKED",
    "APPROVAL_REQUIRED",
    "CRITICAL_RISK",
    "PATH_TRAVERSAL",
    "FORBIDDEN_ARG",
    "SYSTEM_DIRECTORY_WRITE",
    "DANGEROUS_PATH",
    "CREDENTIAL_ACCESS",
    "BUDGET_EXCEEDED",
    "FORBIDDEN_OPERATION",
    "DANGEROUS_OPERATION",
}


# ============================================================================
# Repair Models
# ============================================================================

@dataclass
class RepairEvent:
    """Records a single repair attempt."""
    repaired: bool = False
    node_id: str = ""
    original_action: str = ""
    normalized_action: str = ""
    operation: str | None = None
    # Alias rewrites come only from action_alias.resolve_action_alias.
    source: str = "none"
    repair_attempt: int = 0
    validation_error_before: str = ""
    validation_error_code_before: str = ""
    validation_after: str = ""


@dataclass
class PreExecutionRepairResult:
    """Result of a pre-execution repair attempt."""
    repaired: bool = False
    strategy: str = ""              # "deterministic" | "planner_llm" | "none"
    repaired_nodes: list[Any] | None = None
    repair_events: list[RepairEvent] = field(default_factory=list)
    unrepairable_reason: str = ""
    repair_attempts: int = 0


# ============================================================================
# PreExecutionRepairEngine
# ============================================================================

class PreExecutionRepairEngine:
    """Repairs semantic validation errors before execution fails.

    Only handles safe, deterministic repairs:
      - Action alias normalization
      - Tool name alias correction
      - Enum value patching

    Rejects all security/policy errors, forwarding them as-is.
    """

    def __init__(self):
        self._repair_count = 0
        self._llm_repair_count = 0

    def can_repair(self, error_codes: list[str]) -> bool:
        """Check if any error is repairable and NONE are non-repairable."""
        if not error_codes:
            return False

        has_repairable = any(c in REPAIRABLE_ERROR_CODES for c in error_codes)
        has_blocker = any(c in NON_REPAIRABLE_ERROR_CODES for c in error_codes)

        return has_repairable and not has_blocker

    def try_repair(
        self,
        nodes,
        validation_errors: list[Any],
    ) -> PreExecutionRepairResult:
        """Attempt to repair semantic validation errors in a tool-call batch.

        Args:
            nodes: Normalized QueryLoop tool calls that failed validation
            validation_errors: List of SemanticError objects from SemanticValidator

        Returns:
            PreExecutionRepairResult with repaired_nodes if successful
        """
        events: list[RepairEvent] = []
        error_codes = [e.code for e in validation_errors]

        # Block: any non-repairable error → refuse
        for code in error_codes:
            if code in NON_REPAIRABLE_ERROR_CODES:
                return PreExecutionRepairResult(
                    repaired=False,
                    strategy="none",
                    unrepairable_reason=f"Non-repairable error: {code}",
                    repair_events=events,
                )

        if not self.can_repair(error_codes):
            return PreExecutionRepairResult(
                repaired=False,
                strategy="none",
                unrepairable_reason=f"No repairable errors found in: {error_codes}",
                repair_events=events,
            )

        # Attempt deterministic repair
        self._repair_count += 1

        for error in validation_errors:
            node_id = getattr(error, "node_id", "")
            code = getattr(error, "code", "")
            message = getattr(error, "message", "")
            details = getattr(error, "details", {})

            node = self._find_node(nodes, node_id)
            if node is None:
                continue

            event = RepairEvent(
                node_id=node_id,
                validation_error_code_before=code,
                validation_error_before=message,
                repair_attempt=self._repair_count,
            )

            repaired = False

            if code == "ARG_ENUM_INVALID":
                repaired = self._repair_enum_invalid(node, event, message)

            elif code == "ACTION_ALIAS_NOT_NORMALIZED":
                repaired = self._repair_action_alias_not_normalized(node, event)

            elif code == "MISSING_REQUIRED_ARG":
                # Missing values encode user/model intent. Inventing defaults
                # such as action=list or command="echo ok" can execute the
                # wrong operation. Leave the graph unchanged so QueryLoop can
                # return the structured error to the LLM for correction.
                repaired = False

            elif code in ("INVALID_ACTION_ALIAS", "NODE_ARG_NORMALIZABLE"):
                repaired = self._repair_action_alias(node, event)

            event.repaired = repaired
            events.append(event)

        # Check if all repairable errors were fixed
        any_repaired = any(e.repaired for e in events)
        if not any_repaired:
            return PreExecutionRepairResult(
                repaired=False,
                strategy="deterministic",
                unrepairable_reason="Deterministic repair could not fix any errors",
                repair_events=events,
                repair_attempts=self._repair_count,
            )

        return PreExecutionRepairResult(
            repaired=any_repaired,
            strategy="deterministic",
            repaired_nodes=nodes,
            repair_events=events,
            repair_attempts=self._repair_count,
        )

    # ========================================================================
    # Individual repair methods
    # ========================================================================

    def _repair_enum_invalid(self, node, event: RepairEvent, message: str) -> bool:
        """Fix enum mismatch via action alias normalization.

        Resolution uses the canonical action table only.
        """
        action = node.args.get("action", "")
        if not action or not isinstance(action, str):
            return False

        from .action_alias import resolve_action_alias
        resolution = resolve_action_alias(node.tool, action)
        if resolution.matched:
            event.original_action = resolution.original_action
            event.normalized_action = resolution.canonical_action
            event.operation = resolution.operation
            event.source = resolution.source
            node.args["action"] = resolution.canonical_action
            if resolution.operation:
                node.args["operation"] = resolution.operation
            event.validation_after = "pass"
            return True

        return False

    def _repair_action_alias_not_normalized(self, node, event: RepairEvent) -> bool:
        """Normalize an action alias that the compiler missed.

        Uses the same canonical source as :meth:`_repair_enum_invalid`.
        """
        from .action_alias import resolve_action_alias

        action = node.args.get("action", "")
        if not action or not isinstance(action, str):
            return False

        resolution = resolve_action_alias(node.tool, action)
        if resolution.matched:
            event.original_action = resolution.original_action
            event.normalized_action = resolution.canonical_action
            event.operation = resolution.operation
            event.source = resolution.source
            node.args["action"] = resolution.canonical_action
            if resolution.operation:
                node.args["operation"] = resolution.operation
            event.validation_after = "pass"
            return True

        return False

    def _repair_action_alias(self, node, event: RepairEvent) -> bool:
        """General action alias repair.

        Uses the canonical action alias table.
        """
        action = node.args.get("action", "")
        if not action or not isinstance(action, str):
            return False

        from .action_alias import resolve_action_alias
        resolution = resolve_action_alias(node.tool, action)
        if resolution.matched:
            event.original_action = resolution.original_action
            event.normalized_action = resolution.canonical_action
            event.operation = resolution.operation
            event.source = resolution.source
            node.args["action"] = resolution.canonical_action
            if resolution.operation:
                node.args["operation"] = resolution.operation
            event.validation_after = "pass"
            return True

        return False

    # ========================================================================
    # Helpers
    # ========================================================================

    def _find_node(self, nodes, node_id: str):
        """Find a call by ID in the current batch."""
        if nodes is None:
            return None
        for n in nodes:
            if n.id == node_id:
                return n
        return None

    def should_replan_with_llm(
        self,
        repair_result: PreExecutionRepairResult,
        budget_llm_remaining: int,
    ) -> bool:
        """Check if we should attempt LLM-based replanning."""
        return (
            not repair_result.repaired
            and budget_llm_remaining > 0
            and self._llm_repair_count < 1
        )

    def mark_llm_repair_attempt(self) -> None:
        """Record that an LLM-based repair was attempted."""
        self._llm_repair_count += 1
