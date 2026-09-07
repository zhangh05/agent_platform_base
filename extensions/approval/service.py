"""Durable prepared-operation records owned by the optional approval extension.

This module never classifies command text as dangerous.  It freezes exactly
what a model requested, then later revalidates server-owned scope metadata
before the original call may be executed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from extensions.sdk import ExtensionDataStore
from storage.locking import FileLock

EXTENSION_ID = "approval"
NETWORK_TOOL_ID = "network.operations.device.manage"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store(workspace_id: str) -> ExtensionDataStore:
    return ExtensionDataStore(EXTENSION_ID, workspace_id)


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _frozen_projection(record: dict[str, Any]) -> dict[str, Any]:
    """Return precisely the fields covered by an operation digest."""
    return {
        key: record.get(key)
        for key in (
            "schema", "tool_id", "action", "workspace_id", "session_id", "run_id",
            "request_id", "call_id", "skill", "target", "commands", "timeout",
            "execution_model",
        )
    }


def _public_connection(connection: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(connection, dict):
        return {}
    return {
        key: connection.get(key)
        for key in ("connection_id", "device_id", "name", "protocol", "port", "revision", "auth_method")
    }


def prepare_network_operation(request: dict[str, Any]) -> dict[str, Any] | None:
    """Freeze one authorized configure call without touching the device."""
    context = request.get("workbench_context") if isinstance(request.get("workbench_context"), dict) else {}
    if not bool(context.get("approval_enabled")):
        return None
    if str(request.get("tool_id") or "") != NETWORK_TOOL_ID:
        return None
    arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
    workspace_id = str(request.get("workspace_id") or "").strip()
    connection_id = str(arguments.get("connection_id") or "").strip()
    skill_id = str(context.get("skill_id") or "").strip()
    commands = arguments.get("commands")
    if not workspace_id or not connection_id or not skill_id or not isinstance(commands, list):
        return None
    from extensions.network_operations.device_tools import is_read_only_command
    if commands and all(is_read_only_command(command) for command in commands):
        return None

    from extensions.network_operations import service as network

    skill = network.get_skill(workspace_id, skill_id)
    connection = network.get_connection(workspace_id, connection_id)
    device = network.get_device(workspace_id, str((connection or {}).get("device_id") or ""))
    if not skill or not connection or not device:
        # The regular tool route will return its normal structured scope error.
        return None
    selected_connection_ids = [str(value) for value in (context.get("connection_ids") or [])]
    if connection_id not in selected_connection_ids:
        return None

    frozen = {
        "schema": "lzcore.prepared_operation.v1",
        "tool_id": NETWORK_TOOL_ID,
        "action": "configure",
        "workspace_id": workspace_id,
        "session_id": str(request.get("session_id") or ""),
        "run_id": str(request.get("run_id") or ""),
        "request_id": str(request.get("request_id") or ""),
        "call_id": str(request.get("call_id") or ""),
        "skill": {
            "skill_id": skill_id,
            "updated_at": str(skill.get("updated_at") or ""),
            "enabled": bool(skill.get("enabled", True)),
            "connection_ids": [str(value) for value in (skill.get("connection_ids") or [])],
        },
        "target": {
            "device_id": str(device.get("device_id") or ""),
            "name": str(device.get("name") or ""),
            "host": str(device.get("host") or ""),
            "vendor": str(device.get("vendor") or "generic"),
            "updated_at": str(device.get("updated_at") or ""),
            "connection": _public_connection(connection),
        },
        # Preserve exact UTF-8 command text and ordering.  React renders text
        # as text; the digest is calculated over these exact strings.
        "commands": [str(command) for command in commands],
        "timeout": int(arguments.get("timeout") or 15),
        "execution_model": {
            "apply_mode": "driver_defined",
            "persist_mode": "explicit_only",
            "verification": "model_directed_readback",
            "retry": "reconcile_on_unknown",
        },
    }
    digest = _digest(frozen)
    operation_id = f"appr_{uuid.uuid4().hex}"
    record = {
        **frozen,
        "operation_id": operation_id,
        "digest": digest,
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
        "decision": {},
        "execution": {},
    }
    _store(workspace_id).save("operations", operation_id, record)
    return record


def get_operation(workspace_id: str, operation_id: str) -> dict[str, Any] | None:
    return _store(workspace_id).get("operations", operation_id)


def list_operations(workspace_id: str, *, session_id: str = "", status: str = "") -> list[dict[str, Any]]:
    values = _store(workspace_id).list("operations", limit=1000)
    if session_id:
        values = [item for item in values if str(item.get("session_id") or "") == session_id]
    if status:
        values = [item for item in values if str(item.get("status") or "") == status]
    return sorted(values, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _record_lock(workspace_id: str, operation_id: str) -> FileLock:
    return FileLock(_store(workspace_id).root() / "operations" / f"{operation_id}.lock")


def decide_operation(workspace_id: str, operation_id: str, decision: str, *, decided_by: str = "user", note: str = "") -> dict[str, Any]:
    normalized = str(decision or "").strip().lower()
    if normalized not in {"approve", "reject", "cancel"}:
        raise ValueError("invalid_decision")
    with _record_lock(workspace_id, operation_id):
        record = get_operation(workspace_id, operation_id)
        if not record:
            raise KeyError("operation_not_found")
        status = str(record.get("status") or "")
        if status != "pending":
            return record
        record["status"] = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[normalized]
        record["decision"] = {
            "value": normalized,
            "decided_by": str(decided_by or "user"),
            "note": str(note or ""),
            "decided_at": _now(),
        }
        record["updated_at"] = _now()
        _store(workspace_id).save("operations", operation_id, record)
        return record


def claim_execution(workspace_id: str, operation_id: str) -> dict[str, Any]:
    """Atomically claim an approved record after server-owned scope recheck."""
    with _record_lock(workspace_id, operation_id):
        record = get_operation(workspace_id, operation_id)
        if not record:
            raise KeyError("operation_not_found")
        if record.get("status") != "approved":
            raise ValueError("operation_not_approved")
        if str(record.get("digest") or "") != _digest(_frozen_projection(record)):
            record["status"] = "invalidated"
            record["updated_at"] = _now()
            record["invalidated_reason"] = "prepared_operation_digest_mismatch"
            _store(workspace_id).save("operations", operation_id, record)
            return record
        from extensions.network_operations import service as network
        target = record.get("target") if isinstance(record.get("target"), dict) else {}
        frozen_connection = target.get("connection") if isinstance(target.get("connection"), dict) else {}
        connection_id = str(frozen_connection.get("connection_id") or "")
        current_connection = network.get_connection(workspace_id, connection_id)
        current_device = network.get_device(workspace_id, str(target.get("device_id") or ""))
        skill_data = record.get("skill") if isinstance(record.get("skill"), dict) else {}
        skill = network.get_skill(workspace_id, str(skill_data.get("skill_id") or ""))
        valid = bool(
            current_connection
            and current_device
            and skill
            and skill.get("enabled", True)
            and connection_id in set(skill.get("connection_ids") or [])
            and str(current_connection.get("revision") or "") == str(frozen_connection.get("revision") or "")
            and str(current_device.get("updated_at") or "") == str(target.get("updated_at") or "")
            and str(skill.get("updated_at") or "") == str(skill_data.get("updated_at") or "")
        )
        if not valid:
            record["status"] = "invalidated"
            record["updated_at"] = _now()
            record["invalidated_reason"] = "server_scope_or_connection_changed"
            _store(workspace_id).save("operations", operation_id, record)
            return record
        record["status"] = "executing"
        record["execution"] = {"started_at": _now(), "execution_nonce": uuid.uuid4().hex}
        record["updated_at"] = _now()
        _store(workspace_id).save("operations", operation_id, record)
        return record


def settle_execution(workspace_id: str, operation_id: str, result: dict[str, Any]) -> dict[str, Any]:
    with _record_lock(workspace_id, operation_id):
        record = get_operation(workspace_id, operation_id)
        if not record:
            raise KeyError("operation_not_found")
        if record.get("status") != "executing":
            return record
        record["status"] = "unknown" if result.get("execution_may_continue") else "executed"
        record["execution"] = {
            **dict(record.get("execution") or {}),
            "finished_at": _now(),
            "result": dict(result or {}),
        }
        record["updated_at"] = _now()
        _store(workspace_id).save("operations", operation_id, record)
        return record


def attach_continuation_checkpoint(
    workspace_id: str,
    *,
    session_id: str,
    run_id: str,
    request_id: str,
    user_input: str,
    messages: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    prior_results: list[dict[str, Any]],
    round_results: list[dict[str, Any]],
    interruption_ids: list[str],
    workbench_context: dict[str, Any],
) -> dict[str, Any]:
    """Persist an exact, resumable model boundary for a decision set.

    The checkpoint owns no device session and has no expiry.  It contains the
    full model-visible transcript through the tool-call boundary, not a model
    generated summary.  A later decision supplies the missing tool results and
    resumes this same logical loop.
    """
    checkpoint_id = f"apprchk_{uuid.uuid4().hex}"
    record = {
        "schema": "lzcore.approval_continuation.v1",
        "checkpoint_id": checkpoint_id,
        "workspace_id": workspace_id,
        "session_id": session_id,
        "run_id": run_id,
        "request_id": request_id,
        "user_input": user_input,
        "messages": messages,
        "tool_calls": tool_calls,
        "prior_results": prior_results,
        "round_results": round_results,
        "operation_ids": list(dict.fromkeys(str(value) for value in interruption_ids if str(value))),
        "workbench_context": dict(workbench_context or {}),
        "status": "waiting_decisions",
        "created_at": _now(),
        "updated_at": _now(),
    }
    _store(workspace_id).save("continuations", checkpoint_id, record)
    for operation_id in record["operation_ids"]:
        with _record_lock(workspace_id, operation_id):
            operation = get_operation(workspace_id, operation_id)
            if operation:
                operation["continuation"] = {"checkpoint_id": checkpoint_id}
                operation["updated_at"] = _now()
                _store(workspace_id).save("operations", operation_id, operation)
    return record


def get_continuation(workspace_id: str, checkpoint_id: str) -> dict[str, Any] | None:
    return _store(workspace_id).get("continuations", checkpoint_id)


def _continuation_lock(workspace_id: str, checkpoint_id: str) -> FileLock:
    return FileLock(_store(workspace_id).root() / "continuations" / f"{checkpoint_id}.lock")


def claim_ready_continuation(workspace_id: str, operation_id: str) -> dict[str, Any] | None:
    """Claim a decision set once every frozen call has reached a terminal state.

    One approval may be granted while another remains pending.  In that case
    the original loop stays paused; it never receives a partial fabricated
    answer.  The checkpoint claim is idempotent and prevents two browser tabs
    from resuming the same logical turn.
    """
    operation = get_operation(workspace_id, operation_id)
    continuation = operation.get("continuation") if isinstance(operation, dict) else {}
    checkpoint_id = str((continuation or {}).get("checkpoint_id") or "")
    if not checkpoint_id:
        return None
    with _continuation_lock(workspace_id, checkpoint_id):
        checkpoint = get_continuation(workspace_id, checkpoint_id)
        if not checkpoint or checkpoint.get("status") != "waiting_decisions":
            return None
        operations = [get_operation(workspace_id, item) for item in checkpoint.get("operation_ids") or []]
        if not operations or any(not item for item in operations):
            return None
        terminal = {"executed", "unknown", "invalidated", "rejected", "cancelled"}
        if any(str(item.get("status") or "") not in terminal for item in operations):
            return None
        checkpoint["status"] = "resuming"
        checkpoint["updated_at"] = _now()
        checkpoint["operations"] = operations
        _store(workspace_id).save("continuations", checkpoint_id, checkpoint)
        return checkpoint


def settle_continuation(workspace_id: str, checkpoint_id: str, *, result: dict[str, Any]) -> None:
    with _continuation_lock(workspace_id, checkpoint_id):
        checkpoint = get_continuation(workspace_id, checkpoint_id)
        if not checkpoint:
            return
        checkpoint["status"] = "resumed"
        checkpoint["resumed_at"] = _now()
        checkpoint["resume_result"] = dict(result or {})
        checkpoint["updated_at"] = _now()
        _store(workspace_id).save("continuations", checkpoint_id, checkpoint)


def execution_interceptor(request: dict[str, Any]) -> dict[str, Any] | None:
    record = prepare_network_operation(request)
    if not record:
        return None
    return {
        "action": "suspend",
        "extension_id": EXTENSION_ID,
        "interruption_id": record["operation_id"],
        "kind": "approval",
        "summary": "已准备设备操作，等待审批决定。",
        "payload": {
            "operation_id": record["operation_id"],
            "digest": record["digest"],
            "target": record["target"],
            "command_count": len(record["commands"]),
        },
    }
