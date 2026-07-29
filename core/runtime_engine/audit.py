"""
Audit Log for SSOT Runtime Engine.

Every request generates an immutable audit record with:
  - request-level metadata
  - executed/blocked/failed tool calls
  - sensitive field redaction
  - per-call traceability

Audit records are NOT writable by normal execution flows.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from .models import (
    AuditRecord,
    StatelessContext,
    ToolResult,
)


SENSITIVE_FIELDS: set[str] = {
    "password", "passwd", "token", "secret", "key",
    "credential", "auth", "cookie", "session", "private_key",
    "api_key", "access_key", "bearer",
}


class AuditLogger:
    """Immutable audit log generator."""

    def __init__(self):
        self._records: list[AuditRecord] = []

    def create_record(
        self,
        ctx: StatelessContext,
        node_results: dict[str, ToolResult],
        risk_level: str = "low",
        approval_required: bool = False,
        planner_model: str = "",
        llm_call_count: int = 0,
        duration_ms: float = 0.0,
    ) -> AuditRecord:
        """Generate an immutable audit record for this request."""

        request_hash = hashlib.sha256(
            (ctx.user_input + ctx.request_id).encode()
        ).hexdigest()[:16]

        executed = []
        blocked = []
        failed = []

        for call_id, result in node_results.items():
            entry = {
                "call_id": call_id,
                "tool": result.tool,
                "status": "success" if result.success else "failed",
                "latency_ms": result.latency_ms,
                "retry_count": result.retry_count,
            }
            if result.success:
                entry["result_summary"] = self._redact_result(result.data)
                executed.append(entry)
            else:
                entry["error"] = result.error or "tool_failed"
                failed.append(entry)

        for item in ctx.extras.get("audit_blocked_nodes") or []:
            if not isinstance(item, dict):
                continue
            blocked.append({
                "call_id": str(item.get("call_id") or item.get("node_id") or ""),
                "tool": str(item.get("tool") or ""),
                "args": self._redact_args(dict(item.get("args") or {})),
                "status": "blocked",
                "error": str(item.get("error") or "blocked"),
            })

        record = AuditRecord(
            request_id=ctx.request_id,
            session_id=ctx.session_id,
            created_at=time.time(),
            user_request_hash=request_hash,
            planner_model=planner_model,
            llm_call_count=llm_call_count,
            tool_call_count=len(node_results) + len(blocked),
            risk_level=risk_level,
            approval_required=approval_required,
            executed_nodes=executed,
            blocked_nodes=blocked,
            failed_nodes=failed,
            duration_ms=duration_ms,
        )

        self._records.append(record)
        return record

    def _redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive fields from node args."""
        if not args:
            return {}
        redacted = {}
        for key, value in args.items():
            if key.lower() in SENSITIVE_FIELDS:
                redacted[key] = "***REDACTED***"
            elif isinstance(value, dict):
                redacted[key] = self._redact_args(value)
            elif isinstance(value, str) and any(
                sf in value.lower() for sf in SENSITIVE_FIELDS
            ):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = value
        return redacted

    def _redact_result(self, data: Any) -> Any:
        """Redact sensitive data from results."""
        if isinstance(data, dict):
            return self._redact_args(data)
        if isinstance(data, str) and len(data) > 500:
            return data[:500] + "..."
        return data

    @property
    def records(self) -> list[AuditRecord]:
        return list(self._records)

    def last_record(self) -> AuditRecord | None:
        return self._records[-1] if self._records else None
