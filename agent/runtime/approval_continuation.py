"""Durable continuation records for ordinary interactive Agent approvals."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
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
_TERMINAL_STATUSES = frozenset({"completed", "failed", "rejected", "expired"})
_MAINTENANCE_LOCK = threading.Lock()
_LAST_MAINTENANCE: dict[str, float] = {}


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


def new_continuation_id() -> str:
    return f"cont_{uuid.uuid4().hex}"


def _bounded_env_seconds(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def continuation_stall_seconds() -> int:
    return _bounded_env_seconds(
        "LZCORE_CONTINUATION_STALL_SECONDS", 900, 60, 7 * 24 * 60 * 60
    )


def continuation_retention_seconds() -> int:
    days = _bounded_env_seconds("LZCORE_CONTINUATION_RETENTION_DAYS", 30, 1, 3650)
    return days * 24 * 60 * 60


def create_continuation(
    *,
    workspace_id: str,
    session_id: str,
    parent_run_id: str,
    user_input: str,
    tool_calls: list[dict[str, Any]],
    approval_ids: list[str],
    approved_node_ids: list[str] | None = None,
    continuation_id: str = "",
) -> str:
    if not tool_calls or not approval_ids:
        raise ValueError("continuation_requires_tool_calls_and_approvals")
    maintain_continuations(workspace_id)
    continuation_id = continuation_id or new_continuation_id()
    if not _ID_RE.fullmatch(continuation_id):
        raise ValueError("invalid_continuation_id")
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


def delete_continuation(workspace_id: str, continuation_id: str) -> bool:
    """Compensate a failed aggregate creation before approvals are published."""
    path = _path(workspace_id, continuation_id)
    with FileLock(path.with_suffix(".lock")):
        if not path.is_file():
            return False
        record = _read_unlocked(path)
        if record.get("status") != "pending" or record.get("decisions"):
            raise RuntimeError("continuation_already_active")
        delete_secret(str(record.get("payload_ref") or ""))
        path.unlink()
        return True


def record_decision(
    *,
    workspace_id: str,
    continuation_id: str,
    approval_id: str,
    allowed: bool,
) -> dict[str, Any]:
    """Durably record one Guardian decision without claiming execution.

    Guardian is the approval authority. This idempotent transition is kept
    separate from the owner claim so a reconciler can repair a crash between
    those durable writes without replaying any tool.
    """
    path = _path(workspace_id, continuation_id)
    with FileLock(path.with_suffix(".lock")):
        record = _read_unlocked(path)
        approval_ids = list(record.get("approval_ids") or [])
        if approval_id not in approval_ids:
            raise ValueError("approval_not_bound_to_continuation")
        status = str(record.get("status") or "")
        if status in _TERMINAL_STATUSES | {"claimed", "dispatching", "stalled"}:
            return dict(record)
        if status not in {"pending", "ready"}:
            raise RuntimeError("continuation_invalid_decision_state")
        decisions = dict(record.get("decisions") or {})
        if approval_id in decisions and bool(decisions[approval_id]) != bool(allowed):
            raise RuntimeError("approval_decision_conflict")
        if approval_id in decisions:
            return dict(record)
        decisions[approval_id] = bool(allowed)
        record["decisions"] = decisions
        record["decision_version"] = int(record.get("decision_version") or 0) + 1
        record["updated_at"] = _now_iso()
        if not allowed or not all(bool(decisions.get(aid)) for aid in approval_ids if aid in decisions):
            if not allowed:
                record["status"] = "rejected"
                record["execution_phase"] = "decision_rejected"
                atomic_write_json(path, record)
                delete_secret(str(record.get("payload_ref") or ""))
                return dict(record)
        if approval_ids and all(aid in decisions for aid in approval_ids):
            if all(bool(decisions.get(aid)) for aid in approval_ids):
                record["status"] = "ready"
                record["execution_phase"] = "decision_ready"
        atomic_write_json(path, record)
        return dict(record)


def claim_ready_continuation(
    *,
    workspace_id: str,
    continuation_id: str,
) -> tuple[dict[str, Any], ApprovedToolContinuation | None, dict[str, Any] | None]:
    """CAS claim a fully approved continuation; never replay an active one."""
    path = _path(workspace_id, continuation_id)
    with FileLock(path.with_suffix(".lock")):
        record = _read_unlocked(path)
        if str(record.get("status") or "") != "ready":
            return dict(record), None, None
        approval_ids = list(record.get("approval_ids") or [])
        decisions = dict(record.get("decisions") or {})
        if not approval_ids or any(aid not in decisions for aid in approval_ids):
            return dict(record), None, None
        if not all(bool(decisions.get(aid)) for aid in approval_ids):
            record["status"] = "rejected"
            record["execution_phase"] = "decision_rejected"
            record["updated_at"] = _now_iso()
            atomic_write_json(path, record)
            delete_secret(str(record.get("payload_ref") or ""))
            return dict(record), None, None
        payload_text = get_secret(str(record.get("payload_ref") or ""))
        if not payload_text or hashlib.sha256(payload_text.encode()).hexdigest() != record.get("payload_sha256"):
            record["status"] = "failed"
            record["error"] = "continuation_payload_unavailable_or_corrupt"
            record["updated_at"] = _now_iso()
            atomic_write_json(path, record)
            raise RuntimeError(record["error"])
        payload = json.loads(payload_text)
        _validate_payload(record, payload)
        now = _now_iso()
        record["status"] = "claimed"
        record["claimed_at"] = now
        record["heartbeat_at"] = now
        record["execution_phase"] = "claimed"
        record["updated_at"] = now
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
        if record.get("status") in {"claimed", "dispatching", "stalled"}:
            record["status"] = "failed" if error else "completed"
            record["execution_phase"] = "finished"
            record["completed_run_id"] = completed_run_id
            record["error"] = str(error or "")[:500]
            record["updated_at"] = _now_iso()
            atomic_write_json(path, record)
            delete_secret(str(record.get("payload_ref") or ""))
        return dict(record)


def mark_continuation_dispatching(workspace_id: str, continuation_id: str) -> dict[str, Any]:
    path = _path(workspace_id, continuation_id)
    with FileLock(path.with_suffix(".lock")):
        record = _read_unlocked(path)
        if record.get("status") != "claimed":
            raise RuntimeError("continuation_not_claimed")
        now = _now_iso()
        record["status"] = "dispatching"
        record["execution_phase"] = "dispatching"
        record["dispatch_started_at"] = now
        record["heartbeat_at"] = now
        record["updated_at"] = now
        atomic_write_json(path, record)
        return dict(record)


def heartbeat_continuation(workspace_id: str, continuation_id: str) -> bool:
    path = _path(workspace_id, continuation_id)
    with FileLock(path.with_suffix(".lock")):
        record = _read_unlocked(path)
        if record.get("status") not in {"claimed", "dispatching", "stalled"}:
            return False
        now = _now_iso()
        if record.get("status") == "stalled":
            # A live owner heartbeat proves the original execution is still
            # active; this never creates a new execution or replays a tool.
            record["status"] = "dispatching"
            record.pop("stalled_at", None)
            record.pop("stall_reason", None)
        record["heartbeat_at"] = now
        record["updated_at"] = now
        atomic_write_json(path, record)
        return True


def maintain_continuations(workspace_id: str, *, force: bool = False) -> dict[str, int]:
    """Mark stale work and enforce expiry/retention without replaying tools."""
    now_epoch = time.time()
    with _MAINTENANCE_LOCK:
        previous = _LAST_MAINTENANCE.get(workspace_id, 0.0)
        if not force and now_epoch - previous < 60:
            return {"stalled": 0, "expired": 0, "deleted": 0}
        _LAST_MAINTENANCE[workspace_id] = now_epoch

    from agent.approval import approval_ttl_seconds
    from storage.records import list_json_records

    counters = {"stalled": 0, "expired": 0, "deleted": 0}
    records = list_json_records(workspace_id, ("approvals", "continuations"), limit=5000)
    for snapshot in records:
        continuation_id = str(snapshot.get("continuation_id") or "")
        if not _ID_RE.fullmatch(continuation_id):
            continue
        path = _path(workspace_id, continuation_id)
        with FileLock(path.with_suffix(".lock")):
            if not path.is_file():
                continue
            record = _read_unlocked(path)
            status = str(record.get("status") or "")
            updated_age = _age_seconds(record.get("updated_at"), now_epoch)
            if status == "pending" and _age_seconds(record.get("created_at"), now_epoch) >= approval_ttl_seconds():
                record["status"] = "expired"
                record["error"] = "approval_ttl_expired"
                record["updated_at"] = _now_iso()
                atomic_write_json(path, record)
                delete_secret(str(record.get("payload_ref") or ""))
                counters["expired"] += 1
            elif status in {"claimed", "dispatching"}:
                heartbeat_age = _age_seconds(
                    record.get("heartbeat_at") or record.get("claimed_at") or record.get("updated_at"),
                    now_epoch,
                )
                if heartbeat_age >= continuation_stall_seconds():
                    record["status"] = "stalled"
                    record["stalled_at"] = _now_iso()
                    record["stall_reason"] = "execution_heartbeat_expired"
                    record["updated_at"] = record["stalled_at"]
                    atomic_write_json(path, record)
                    counters["stalled"] += 1
            elif status in _TERMINAL_STATUSES and updated_age >= continuation_retention_seconds():
                delete_secret(str(record.get("payload_ref") or ""))
                path.unlink()
                counters["deleted"] += 1
    return counters


def list_continuations(
    workspace_id: str, *, status: str = "", limit: int = 100
) -> list[dict[str, Any]]:
    maintain_continuations(workspace_id)
    from storage.records import list_json_records

    records = list_json_records(
        workspace_id, ("approvals", "continuations"), limit=5000
    )
    public: list[dict[str, Any]] = []
    for record in records:
        if status and str(record.get("status") or "") != status:
            continue
        approval_ids = list(record.get("approval_ids") or [])
        decisions = dict(record.get("decisions") or {})
        public.append({
            key: record.get(key)
            for key in (
                "continuation_id", "workspace_id", "session_id", "parent_run_id",
                "status", "execution_phase", "created_at", "updated_at", "claimed_at",
                "dispatch_started_at", "heartbeat_at", "stalled_at", "stall_reason",
                "completed_run_id", "error",
            )
            if record.get(key) not in (None, "")
        } | {
            "approval_count": len(approval_ids),
            "decision_count": len(decisions),
        })
    return public[: min(max(limit, 1), 5000)]


def close_stalled_continuation(
    workspace_id: str, continuation_id: str, *, reason: str
) -> dict[str, Any]:
    """Close an unknown-outcome execution after explicit operator review."""
    path = _path(workspace_id, continuation_id)
    with FileLock(path.with_suffix(".lock")):
        record = _read_unlocked(path)
        if record.get("status") != "stalled":
            raise RuntimeError("continuation_not_stalled")
        record["status"] = "failed"
        record["execution_phase"] = "operator_closed"
        record["error"] = f"operator_closed:{str(reason or 'no_reason')[:400]}"
        record["updated_at"] = _now_iso()
        atomic_write_json(path, record)
        delete_secret(str(record.get("payload_ref") or ""))
        return dict(record)


def _age_seconds(raw: Any, now_epoch: float) -> float:
    try:
        text = str(raw or "").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, now_epoch - parsed.timestamp())
    except (TypeError, ValueError):
        return float("inf")


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


def reconcile_decisions_from_guardian(
    workspace_id: str,
    approval_records: list[dict[str, Any]],
) -> dict[str, int]:
    """Repair missing continuation decisions from durable Guardian records.

    This function only writes continuation state. It never claims, dispatches,
    resumes, or invokes a tool, so startup/periodic reconciliation remains
    fail-closed for side effects.
    """
    from storage.records import list_json_records

    latest: dict[str, dict[str, Any]] = {}
    for raw in approval_records:
        if not isinstance(raw, dict) or not raw.get("resolved"):
            continue
        approval_id = str(raw.get("approval_id") or "")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        if not approval_id or str(raw.get("workspace_id") or "") != workspace_id:
            continue
        latest[approval_id] = raw
    counters = {"decision_repaired": 0, "decision_mismatch": 0, "ready": 0}
    for raw in list_json_records(workspace_id, ("approvals", "continuations"), limit=5000):
        if not isinstance(raw, dict):
            continue
        continuation_id = str(raw.get("continuation_id") or "")
        status = str(raw.get("status") or "")
        if not continuation_id or status not in {"pending", "ready"}:
            continue
        expected_ids = list(raw.get("approval_ids") or [])
        decisions = dict(raw.get("decisions") or {})
        for approval_id in expected_ids:
            durable = latest.get(str(approval_id))
            if not durable:
                continue
            metadata = durable.get("metadata") if isinstance(durable.get("metadata"), dict) else {}
            if str(metadata.get("continuation_id") or "") != continuation_id:
                counters["decision_mismatch"] += 1
                continue
            allowed = bool(durable.get("allowed"))
            existing = decisions.get(approval_id)
            if existing is not None and bool(existing) != allowed:
                counters["decision_mismatch"] += 1
                continue
            if existing is None:
                record_decision(
                    workspace_id=workspace_id,
                    continuation_id=continuation_id,
                    approval_id=approval_id,
                    allowed=allowed,
                )
                counters["decision_repaired"] += 1
                decisions[approval_id] = allowed
        if expected_ids and all(aid in decisions and bool(decisions[aid]) for aid in expected_ids):
            counters["ready"] += 1
    return counters
