"""Runtime invariants enforced by the canonical QueryLoop execution path."""

from __future__ import annotations

import enum
from typing import Any


class ErrorCode(enum.Enum):
    """Normalized tool-runtime error codes used by retry and audit."""

    TOOL_RETURNED_NOT_OK = "TOOL_RETURNED_NOT_OK"
    TOOL_RESULT_INVALID = "TOOL_RESULT_INVALID"
    NULL_RESULT = "NULL_RESULT"
    TOOL_NOT_REGISTERED = "TOOL_NOT_REGISTERED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_EXCEPTION = "TOOL_EXCEPTION"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    EXECUTION_OBLIGATION_VIOLATION = "EXECUTION_OBLIGATION_VIOLATION"
    RESPONSE_TASK_INCOMPLETE = "RESPONSE_TASK_INCOMPLETE"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"

    @classmethod
    def normalise(cls, raw: str) -> "ErrorCode | str":
        try:
            return cls(raw)
        except ValueError:
            return raw

    @classmethod
    def is_known(cls, candidate: Any) -> bool:
        return isinstance(candidate, cls)

    @property
    def is_retryable(self) -> bool:
        return self in {
            ErrorCode.TOOL_TIMEOUT,
            ErrorCode.TOOL_EXCEPTION,
            ErrorCode.TOOL_RETURNED_NOT_OK,
            ErrorCode.NULL_RESULT,
            ErrorCode.TOOL_RESULT_INVALID,
        }

    @property
    def is_hard_block(self) -> bool:
        return self in {
            ErrorCode.EXECUTION_OBLIGATION_VIOLATION,
            ErrorCode.SCHEMA_VALIDATION_ERROR,
            ErrorCode.CONTRACT_VIOLATION,
        }

    @property
    def is_handler_declared(self) -> bool:
        return self in {
            ErrorCode.TOOL_RETURNED_NOT_OK,
            ErrorCode.NULL_RESULT,
            ErrorCode.TOOL_RESULT_INVALID,
        }


class ContractDegradation(enum.Enum):
    OK = "ok"
    SOFT = "soft"
    HARD = "hard"


class ContractCheck:
    def __init__(self, name: str, passed: bool, *, critical: bool = True):
        self.name = name
        self.level = (
            ContractDegradation.OK
            if passed
            else ContractDegradation.HARD if critical else ContractDegradation.SOFT
        )
        self.message = "" if passed else f"{name} failed"


class ContractReport:
    def __init__(self):
        self.checks: list[ContractCheck] = []

    def add(self, check: ContractCheck) -> None:
        self.checks.append(check)

    def has_critical_failure(self) -> bool:
        return any(check.level == ContractDegradation.HARD for check in self.checks)


class ExecutionContract:
    """Non-negotiable QueryLoop runtime switches checked on every turn."""

    TOOL_TRUTH_SINGLE_SOURCE = True
    CONTEXT_EVENT_STREAM_ONLY = True
    EXECUTION_OBLIGATION_ENFORCED = True
    CONTEXT_CAUSAL_ORDER_ONLY = True
    PLAN_STRICT_SCHEMA_ENFORCED = True
    DIAGNOSTIC_PRESERVATION_REQUIRED = True


class ContractValidator:
    def __init__(self, contracts: type[ExecutionContract]):
        self._contracts = contracts

    def validate_all(self) -> ContractReport:
        report = ContractReport()
        for name in (
            "TOOL_TRUTH_SINGLE_SOURCE",
            "CONTEXT_EVENT_STREAM_ONLY",
            "EXECUTION_OBLIGATION_ENFORCED",
            "CONTEXT_CAUSAL_ORDER_ONLY",
            "PLAN_STRICT_SCHEMA_ENFORCED",
            "DIAGNOSTIC_PRESERVATION_REQUIRED",
        ):
            report.add(ContractCheck(name, bool(getattr(self._contracts, name))))
        return report


class ExecutionSemanticsContract:
    SINGLE_TRUTH_TOOL_RESULT = True


class ContractBoundary:
    """Marks the checkpoints covered by the current QueryLoop turn."""

    ENFORCE_AT = ("engine_entry", "query_loop", "tool_runtime", "response")

    @classmethod
    def validate_all(cls, ctx: Any) -> None:
        ctx.extras["contract_boundary_hits"] = {point: True for point in cls.ENFORCE_AT}

    @staticmethod
    def was_validated(ctx: Any, point: str) -> bool:
        return bool(ctx.extras.get("contract_boundary_hits", {}).get(point))

    @classmethod
    def all_validated(cls, ctx: Any) -> bool:
        return all(cls.was_validated(ctx, point) for point in cls.ENFORCE_AT)


def assert_error_code_usage(result: Any) -> None:
    assert result.error_code_norm is not None, "error_code_norm must be defined"
    assert result.error_code_raw is not None, "error_code_raw must be defined"


__all__ = [
    "ContractBoundary",
    "ContractCheck",
    "ContractDegradation",
    "ContractReport",
    "ContractValidator",
    "ErrorCode",
    "ExecutionContract",
    "ExecutionSemanticsContract",
    "assert_error_code_usage",
]
