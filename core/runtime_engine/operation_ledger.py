"""Durable, redacted operation state for side-effecting canonical tools."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from storage.atomic_io import atomic_write_json
from storage.locking import FileLock
from storage.records import workspace_record_file
from storage.redaction import redact_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def operation_id(workspace_id: str, turn_id: str, call_id: str) -> str:
    source = f"{workspace_id}:{turn_id}:{call_id}"
    return f"op_{hashlib.sha256(source.encode()).hexdigest()[:24]}"


def _path(workspace_id: str, op_id: str):
    return workspace_record_file(workspace_id, "operations", f"{op_id}.json")


def _digest(arguments: dict[str, Any]) -> str:
    raw = json.dumps(arguments or {}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def plan_operation(ctx, tool_id: str, call_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    workspace_id = str(getattr(ctx, "workspace_id", ""))
    turn_id = str(getattr(ctx, "request_id", ""))
    session_id = str(getattr(ctx, "session_id", ""))
    extras = getattr(ctx, "extras", {}) or {}
    op_id = operation_id(workspace_id, turn_id, call_id)
    path = _path(workspace_id, op_id)
    with FileLock(path.with_suffix(".lock")):
        if path.is_file():
            return json.loads(path.read_text())
        now = _now()
        record = {
            "schema": "lzcore.operation_ledger.v1",
            "operation_id": op_id,
            "turn_id": turn_id,
            "workspace_id": workspace_id,
            "session_id": session_id,
            "canonical_tool": tool_id,
            "call_id": call_id,
            "arguments_sha256": _digest(arguments),
            "read_only": False,
            "risk_level": str(extras.get("risk_level") or "unknown"),
            "approval_continuation_id": str(extras.get("approval_continuation_id") or ""),
            "idempotency": "unknown",
            "status": "planned",
            "planned_at": now,
            "updated_at": now,
        }
        atomic_write_json(path, record)
        return record


def start_operation(workspace_id: str, op_id: str) -> dict[str, Any]:
    path = _path(workspace_id, op_id)
    with FileLock(path.with_suffix(".lock")):
        record = json.loads(path.read_text())
        if record.get("status") != "planned":
            return record
        now = _now()
        record.update({"status": "running", "started_at": now, "updated_at": now})
        atomic_write_json(path, record)
        return record


def finish_operation(workspace_id: str, op_id: str, result) -> dict[str, Any]:
    path = _path(workspace_id, op_id)
    with FileLock(path.with_suffix(".lock")):
        record = json.loads(path.read_text())
        if record.get("status") not in {"planned", "running"}:
            return record
        may_continue = bool(getattr(result, "execution_may_continue", False))
        output = getattr(result, "output", {}) or {}
        not_started = output.get("executed") is False
        status = (
            "unknown" if may_continue else
            "blocked" if not_started else
            "succeeded" if bool(getattr(result, "ok", False)) else "failed"
        )
        now = _now()
        record.update({
            "status": status,
            "finished_at": now,
            "updated_at": now,
            "error_code": str(getattr(result, "error_code", "") or "")[:120],
            "error": redact_text(str(getattr(result, "error", "") or ""))[:500],
            "result_summary": redact_text(str(output.get("summary") or ""))[:800],
        })
        atomic_write_json(path, record)
        return record


def list_operations(
    workspace_id: str,
    *,
    status: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return redacted durable write-operation summaries for diagnostics only."""
    from storage.records import list_json_records

    records = list_json_records(workspace_id, ("operations",), limit=5000)
    if status:
        records = [item for item in records if str(item.get("status") or "") == status]
    return [_public_operation_record(item) for item in records[:max(1, min(limit, 500))]]


def _public_operation_record(record: dict[str, Any]) -> dict[str, Any]:
    """Expose a stable, secret-free ledger projection; never return arguments."""
    allowed = (
        "schema", "operation_id", "turn_id", "workspace_id", "session_id",
        "canonical_tool", "call_id", "read_only", "risk_level",
        "approval_continuation_id", "idempotency", "status", "planned_at",
        "started_at", "finished_at", "updated_at", "error_code",
    )
    public = {key: record[key] for key in allowed if key in record}
    public["error"] = redact_text(str(record.get("error") or ""))[:500]
    public["result_summary"] = redact_text(str(record.get("result_summary") or ""))[:800]
    return public
