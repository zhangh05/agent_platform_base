"""Durable redacted audit sidecars derived from authoritative run projections."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage.atomic_io import atomic_write_json
from storage.locking import FileLock
from storage.records import workspace_record_file
from storage.redaction import redact_value

_PAYLOAD_FIELDS = frozenset({
    "turn_id", "trace_id", "status", "execution_outcome", "tool_execution_outcome", "tool_calls",
    "tool_decision", "metadata", "warnings",
})


def write_audit_record(workspace_id: str, run_id: str, payload: dict[str, Any]) -> str:
    """Atomically upsert a compact audit sidecar with a final redaction gate."""
    audit_id = f"audit_{run_id}"
    path = workspace_record_file(workspace_id, "audits", f"{audit_id}.json")
    now = datetime.now(timezone.utc).isoformat()
    safe_payload = redact_value({
        key: value for key, value in dict(payload or {}).items()
        if key in _PAYLOAD_FIELDS
    })
    with FileLock(path.with_suffix(".lock")):
        record = {
            **safe_payload,
            "schema": "lzcore.audit_record.v1",
            "audit_id": audit_id,
            "run_id": run_id,
            "workspace_id": workspace_id,
            "updated_at": now,
        }
        atomic_write_json(path, record)
    return audit_id
