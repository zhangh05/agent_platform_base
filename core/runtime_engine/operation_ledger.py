"""Durable, redacted operation state for side-effecting canonical tools."""
from __future__ import annotations

import hashlib
import json
import re
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
    if not re.fullmatch(r"op_[a-f0-9]{24}", str(op_id or "")):
        raise ValueError("invalid_operation_id")
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
        if record.get("status") not in {"planned", "running", "unknown"}:
            return record
        may_continue = bool(getattr(result, "execution_may_continue", False))
        output = getattr(result, "output", {}) or {}
        not_started = output.get("executed") is False
        status = (
            "unknown" if may_continue else
            "blocked" if not_started else
            "succeeded" if bool(getattr(result, "ok", False)) else "failed"
        )
        if record.get("status") == "unknown" and status == "unknown":
            return record
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


def link_operation_resource(
    workspace_id: str,
    op_id: str,
    *,
    resource_kind: str,
    resource_id: str,
) -> dict[str, Any] | None:
    """Attach a durable resource identity without exposing it to model input."""
    kind = str(resource_kind or "").strip().lower()
    identifier = str(resource_id or "").strip()
    if not kind or not identifier:
        return None
    path = _path(workspace_id, op_id)
    if not path.is_file():
        return None
    with FileLock(path.with_suffix(".lock")):
        record = json.loads(path.read_text())
        current_kind = str(record.get("resource_kind") or "")
        current_id = str(record.get("resource_id") or "")
        if (current_kind or current_id) and (current_kind != kind or current_id != identifier):
            raise RuntimeError("operation_resource_conflict")
        record.update({
            "resource_kind": kind,
            "resource_id": identifier,
            "updated_at": _now(),
        })
        atomic_write_json(path, record)
        return record


def settle_operation(
    workspace_id: str,
    op_id: str,
    *,
    status: str,
    resolved_by: str,
    error_code: str = "",
    error: str = "",
    result_summary: str = "",
    resource_kind: str = "",
    resource_id: str = "",
    resolution_reason: str = "",
    require_unresolved: bool = False,
) -> dict[str, Any]:
    """Resolve planned/running/unknown state from durable or human evidence."""
    if status not in {"succeeded", "failed", "blocked", "reconciled"}:
        raise ValueError("invalid_operation_resolution_status")
    path = _path(workspace_id, op_id)
    if not path.is_file():
        raise FileNotFoundError(op_id)
    with FileLock(path.with_suffix(".lock")):
        record = json.loads(path.read_text())
        current = str(record.get("status") or "")
        if current in {"succeeded", "failed", "blocked"}:
            if require_unresolved:
                raise RuntimeError("operation_already_resolved")
            return record
        if current not in {"planned", "running", "unknown"}:
            raise RuntimeError("operation_not_resolvable")
        now = _now()
        record.update({
            "status": status,
            "finished_at": str(record.get("finished_at") or now),
            "resolved_at": now,
            "resolved_by": str(resolved_by or "system")[:80],
            "resolution_reason": redact_text(str(resolution_reason or ""))[:500],
            "error_code": str(error_code or "")[:120] if status not in {"succeeded", "reconciled"} else "",
            "error": redact_text(str(error or ""))[:500] if status not in {"succeeded", "reconciled"} else "",
            "result_summary": redact_text(str(result_summary or ""))[:800],
            "updated_at": now,
        })
        if resource_kind and resource_id:
            record["resource_kind"] = str(resource_kind)[:80]
            record["resource_id"] = str(resource_id)[:160]
        atomic_write_json(path, record)
        return record


def resolve_operation_manually(
    workspace_id: str,
    op_id: str,
    *,
    status: str,
    reason: str,
) -> dict[str, Any]:
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("resolution_reason_required")
    return settle_operation(
        workspace_id, op_id,
        status=status,
        resolved_by="manual",
        resolution_reason=reason,
        result_summary="人工核对完成",
        require_unresolved=True,
    )


def reconcile_operations(workspace_id: str) -> dict[str, int]:
    """Resolve uncertain operations from their exact durable resource links."""
    records = list_operations(workspace_id, limit=5000)
    outcome = {"checked": 0, "resolved": 0, "pending": 0}
    for record in records:
        if record.get("status") not in {"running", "unknown"}:
            continue
        outcome["checked"] += 1
        kind = str(record.get("resource_kind") or "")
        identifier = str(record.get("resource_id") or "")
        if not kind or not identifier:
            outcome["pending"] += 1
            continue
        resource_status = ""
        summary = ""
        if kind == "subagent":
            from agent.runtime.durable.subagent import get_subagent_task
            resource = get_subagent_task(workspace_id, identifier)
            if resource:
                resource_status = str(resource.get("status") or "")
                summary = str(resource.get("summary") or "")
        elif kind == "job":
            from jobs.store import get_job
            resource = get_job(workspace_id, identifier)
            if resource:
                resource_status = str(resource.status or "")
                summary = str((resource.result_summary or {}).get("status") or resource.error or "")
        if resource_status not in {"succeeded", "failed", "cancelled", "canceled"}:
            outcome["pending"] += 1
            continue
        settle_operation(
            workspace_id,
            str(record["operation_id"]),
            status="succeeded" if resource_status == "succeeded" else "failed",
            resolved_by="durable_resource",
            resource_kind=kind,
            resource_id=identifier,
            result_summary=summary,
            error_code="" if resource_status == "succeeded" else f"{kind.upper()}_{resource_status.upper()}",
        )
        outcome["resolved"] += 1
    return outcome


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
    return [_public_operation_record(item) for item in records[:max(1, min(limit, 5000))]]


def operation_counts(workspace_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in list_operations(workspace_id, limit=5000):
        state = str(record.get("status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def reconcile_all_operations() -> dict[str, dict[str, int]]:
    """Reconcile every principal/workspace after restart without crossing scope."""
    from backend.core.identity import get_user
    from storage.principal import known_storage_principals, storage_principal
    from storage.workspace_store import list_workspace_ids

    results: dict[str, dict[str, int]] = {}
    fallback_workspaces = list_workspace_ids(include_system=False) or ["default"]
    for principal in known_storage_principals() or [""]:
        identity = get_user(principal)
        workspace_ids = list(identity.get("workspace_ids") or []) if isinstance(identity, dict) else fallback_workspaces
        with storage_principal(principal):
            for workspace_id in sorted(set(workspace_ids)):
                results[f"{principal}:{workspace_id}"] = reconcile_operations(workspace_id)
    return results


def _public_operation_record(record: dict[str, Any]) -> dict[str, Any]:
    """Expose a stable, secret-free ledger projection; never return arguments."""
    allowed = (
        "schema", "operation_id", "turn_id", "workspace_id", "session_id",
        "canonical_tool", "call_id", "read_only", "risk_level",
        "idempotency", "status", "planned_at",
        "started_at", "finished_at", "updated_at", "error_code",
        "resource_kind", "resource_id", "resolved_at", "resolved_by",
        "resolution_reason",
    )
    public = {key: record[key] for key in allowed if key in record}
    public["error"] = redact_text(str(record.get("error") or ""))[:500]
    public["result_summary"] = redact_text(str(record.get("result_summary") or ""))[:800]
    return public
