"""
Structured error system for SSOT Runtime Engine.

No bare exception strings allowed. Every error is a SSOTRuntimeError with
code, message, stage context, and retryability.

Bank-grade requirement: errors must be structured, not raw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# --- Error Codes ---

class SSOTRuntimeErrorCode:
    """Canonical error codes for all SSOT Runtime failures."""

    PLANNER_JSON_PARSE = "PLANNER_JSON_PARSE"
    PLANNER_MISSING_FIELD = "PLANNER_MISSING_FIELD"
    PLANNER_TIMEOUT = "PLANNER_TIMEOUT"
    PLANNER_INVALID_OUTPUT = "PLANNER_INVALID_OUTPUT"
    PLANNER_EMPTY_FOR_TASK_INTENT = "PLANNER_EMPTY_FOR_TASK_INTENT"

    RESPONSE_TASK_INCOMPLETE = "RESPONSE_TASK_INCOMPLETE"

    COMPILE_CYCLIC_GRAPH = "COMPILE_CYCLIC_GRAPH"
    COMPILE_MISSING_DEP = "COMPILE_MISSING_DEP"
    COMPILE_INVALID_NODE = "COMPILE_INVALID_NODE"

    VALIDATION_TOOL_NOT_FOUND = "VALIDATION_TOOL_NOT_FOUND"
    VALIDATION_ARG_SCHEMA = "VALIDATION_ARG_SCHEMA"
    VALIDATION_MISSING_INPUT = "VALIDATION_MISSING_INPUT"
    VALIDATION_CONTRACT_MISMATCH = "VALIDATION_CONTRACT_MISMATCH"
    VALIDATION_HIDDEN_DEP = "VALIDATION_HIDDEN_DEP"
    VALIDATION_UNSAFE_OPERATION = "VALIDATION_UNSAFE_OPERATION"

    RISK_CRITICAL_DENIED = "RISK_CRITICAL_DENIED"
    RISK_APPROVAL_REQUIRED = "RISK_APPROVAL_REQUIRED"
    RISK_COMBO_ESCALATION = "RISK_COMBO_ESCALATION"

    BUDGET_NODES_EXCEEDED = "BUDGET_NODES_EXCEEDED"
    BUDGET_DEPTH_EXCEEDED = "BUDGET_DEPTH_EXCEEDED"
    BUDGET_WIDTH_EXCEEDED = "BUDGET_WIDTH_EXCEEDED"
    BUDGET_TIME_EXCEEDED = "BUDGET_TIME_EXCEEDED"
    BUDGET_LLM_EXCEEDED = "BUDGET_LLM_EXCEEDED"

    EXECUTION_TOOL_TIMEOUT = "EXECUTION_TOOL_TIMEOUT"
    EXECUTION_TOOL_EXCEPTION = "EXECUTION_TOOL_EXCEPTION"
    EXECUTION_INVALID_OUTPUT = "EXECUTION_INVALID_OUTPUT"
    EXECUTION_MISSING_DEP_OUTPUT = "EXECUTION_MISSING_DEP_OUTPUT"
    EXECUTION_SCHEMA_MISMATCH = "EXECUTION_SCHEMA_MISMATCH"
    EXECUTION_POLICY_BLOCKED = "EXECUTION_POLICY_BLOCKED"
    EXECUTION_BUDGET_EXCEEDED = "EXECUTION_BUDGET_EXCEEDED"

    REPAIR_RETRY_EXHAUSTED = "REPAIR_RETRY_EXHAUSTED"
    REPAIR_NON_IDEMPOTENT = "REPAIR_NON_IDEMPOTENT"
    REPAIR_CRITICAL_PATH_BROKEN = "REPAIR_CRITICAL_PATH_BROKEN"

    ROLLBACK_UNAVAILABLE = "ROLLBACK_UNAVAILABLE"
    ROLLBACK_CRITICAL_MUTATION = "ROLLBACK_CRITICAL_MUTATION"

    SCHEDULER_CONCURRENCY_LIMIT = "SCHEDULER_CONCURRENCY_LIMIT"
    SCHEDULER_GROUP_LIMIT = "SCHEDULER_GROUP_LIMIT"

    AUDIT_RECORD_FAILED = "AUDIT_RECORD_FAILED"


# --- Structured Error ---

@dataclass
class SSOTRuntimeError:
    """Structured runtime error — never throw raw strings."""
    code: str
    message: str
    stage: str = ""
    node_id: str | None = None
    retryable: bool = False
    risk_level: str = "low"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
            "node_id": self.node_id,
            "retryable": self.retryable,
            "risk_level": self.risk_level,
            "details": self.details,
        }


def build_error(
    code: str,
    message: str,
    stage: str = "",
    node_id: str | None = None,
    retryable: bool = False,
    risk_level: str = "low",
    **details,
) -> SSOTRuntimeError:
    """Factory for structured errors."""
    return SSOTRuntimeError(
        code=code,
        message=message,
        stage=stage,
        node_id=node_id,
        retryable=retryable,
        risk_level=risk_level,
        details=details,
    )
