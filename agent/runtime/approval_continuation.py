"""Durable continuation records for ordinary interactive Agent approvals."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from core.runtime_engine.models import ApprovedToolContinuation
from storage.atomic_io import atomic_write_json
from storage.locking import FileLock
from storage.records import workspace_record_file
from storage.secret_store import delete_secret, get_secret, set_secret

_ID_RE = re.compile(r"^cont_[0-9a-f]{32}$")
_SCHEMA = "agent.approval_continuation.v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(workspace_id: str, continuation_id: str):
    if not _ID_RE.fullmatch(str(continuation_id or "")):
        raise ValueError("invalid_continuation_id")
    return workspace_record_file(
        workspace_id, "approvals", "continuations", f"{continuation_id}.json"
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def create_continuation(
    *,
    workspace_id: str,
    session_id: str,
    parent_run_id: str,
    user_input: str,
    tool_calls: list[dict[str, Any]],
    approval_ids: list[str],
    approved_node_ids: list[str] | None = None,
) -> str:
    if not tool_calls or not approval_ids:
        raise ValueError("continuation_requires_tool_calls_and_approvals")
    continuation_id = f"cont_{uuid.uuid4().hex}"
    call_ids = [str(item.get("id") or "") for item in tool_calls]
    approved_ids = list(dict.fromkeys(approved_node_ids or call_ids))
    if not approved_ids or any(node_id not in call_ids for node_id in approved_ids):
        raise ValueError("invalid_approved_node_ids")
    payload = {
        "schema": _SCHEMA,
        "continuation_id": continuation_id,
        "workspace_id": workspace_id,
        "session_id": session_id,
        "parent_run_id": parent_run_id,
        "user_input": user_input,
        "tool_calls": tool_calls,
        "approved_node_ids": approved_ids,
    }
    payload_text = _canonical(payload)
    secret_ref = set_secret(f"approval_continuation_{continuation_id}", payload_text)
    record = {
        "schema": _SCHEMA,
        "continuation_id": continuation_id,
        "workspace_id": workspace_id,
        "session_id": session_id,
        "parent_run_id": parent_run_id,
        "approval_ids": list(dict.fromkeys(approval_ids)),
        "decisions": {},
        "payload_ref": secret_ref,
        "payload_sha256": hashlib.sha256(payload_text.encode()).hexdigest(),
        "status": "pending",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    path = _path(workspace_id, continuation_id)
    try:
        with FileLock(path.with_suffix(".lock")):
            atomic_write_json(path, record)
    except Exception:
        delete_secret(secret_ref)
        raise
    return continuation_id


def bind_approvals(
    workspace_id: str,
    continuation_id: str,
    approval_ids: list[str],
) -> None:
    """Bind approval ids after their records have been created."""
    path = _path(workspace_id, continuation_id)
    with FileLock(path.with_suffix(".lock")):
        record = _read_unlocked(path)
        if record.get("status") != "pending":
            raise RuntimeError("continuation_not_pending")
        record["approval_ids"] = list(dict.fromkeys(approval_ids))
        record["updated_at"] = _now_iso()
        atomic_write_json(path, record)


def record_decision_and_claim(
    *,
    workspace_id: str,
    continuation_id: str,
    approval_id: str,
    allowed: bool,
) -> tuple[dict[str, Any], ApprovedToolContinuation | None, dict[str, Any] | None]:
    """Record one decision and atomically claim the continuation when ready.

    A continuation moves from ``pending`` to ``running`` exactly once. A
    process crash after that claim fails closed: it is never automatically
    replayed, avoiding duplicate mutations.
    """
    path = _path(workspace_id, continuation_id)
    with FileLock(path.with_suffix(".lock")):
        record = _read_unlocked(path)
        approval_ids = list(record.get("approval_ids") or [])
        if approval_id not in approval_ids:
            raise ValueError("approval_not_bound_to_continuation")
        status = str(record.get("status") or "")
        if status != "pending":
            return dict(record), None, None
        decisions = dict(record.get("decisions") or {})
        decisions[approval_id] = bool(allowed)
        record["decisions"] = decisions
        record["updated_at"] = _now_iso()
        if not allowed:
            record["status"] = "rejected"
            atomic_write_json(path, record)
            delete_secret(str(record.get("payload_ref") or ""))
            return dict(record), None, None
        if not approval_ids or any(aid not in decisions for aid in approval_ids):
            atomic_write_json(path, record)
            return dict(record), None, None
        if not all(bool(decisions.get(aid)) for aid in approval_ids):
            record["status"] = "rejected"
            atomic_write_json(path, record)
            delete_secret(str(record.get("payload_ref") or ""))
            return dict(record), None, None

        payload_text = get_secret(str(record.get("payload_ref") or ""))
        if not payload_text or hashlib.sha256(payload_text.encode()).hexdigest() != record.get("payload_sha256"):
            record["status"] = "failed"
            record["error"] = "continuation_payload_unavailable_or_corrupt"
            atomic_write_json(path, record)
            raise RuntimeError(record["error"])
        payload = json.loads(payload_text)
        _validate_payload(record, payload)
        record["status"] = "running"
        record["claimed_at"] = _now_iso()
        record["updated_at"] = _now_iso()
        atomic_write_json(path, record)
        grant = ApprovedToolContinuation(
            continuation_id=continuation_id,
            tool_calls=tuple(dict(item) for item in payload["tool_calls"]),
            approved_node_ids=tuple(str(item) for item in payload["approved_node_ids"]),
        )
        return dict(record), grant, payload


def finish_continuation(
    workspace_id: str,
    continuation_id: str,
    *,
    completed_run_id: str = "",
    error: str = "",
) -> dict[str, Any]:
    path = _path(workspace_id, continuation_id)
    with FileLock(path.with_suffix(".lock")):
        record = _read_unlocked(path)
        if record.get("status") == "running":
            record["status"] = "failed" if error else "completed"
            record["completed_run_id"] = completed_run_id
            record["error"] = str(error or "")[:500]
            record["updated_at"] = _now_iso()
            atomic_write_json(path, record)
            delete_secret(str(record.get("payload_ref") or ""))
        return dict(record)


def _read_unlocked(path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError("approval_continuation_not_found")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != _SCHEMA:
        raise ValueError("invalid_approval_continuation_record")
    return value


def _validate_payload(record: dict[str, Any], payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
        raise ValueError("invalid_continuation_payload")
    for key in ("continuation_id", "workspace_id", "session_id", "parent_run_id"):
        if str(payload.get(key) or "") != str(record.get(key) or ""):
            raise ValueError(f"continuation_payload_binding_mismatch:{key}")
    calls = payload.get("tool_calls")
    if not isinstance(calls, list) or not calls or not all(isinstance(item, dict) for item in calls):
        raise ValueError("invalid_continuation_tool_calls")
    call_ids = {str(item.get("id") or "") for item in calls}
    approved_ids = payload.get("approved_node_ids")
    if (
        not isinstance(approved_ids, list)
        or not approved_ids
        or any(str(node_id) not in call_ids for node_id in approved_ids)
    ):
        raise ValueError("invalid_continuation_approved_nodes")
