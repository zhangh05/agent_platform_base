"""Storage-owned artifact metadata projection helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any

from storage.atomic_io import atomic_write_json, atomic_write_text
from storage.ids import validate_workspace_id
from storage.locking import FileLock
from storage.paths import workspace_root
from storage.time_utils import now_iso


def create_artifact_metadata(
    *,
    workspace_id: str,
    file_record: Any,
    artifact_type: str,
    title: str,
    scope: str = "workspace",
    sensitivity: str = "internal",
    run_id: str = "",
    session_id: str = "",
    source: str = "storage",
    metadata: dict | None = None,
    created_by: str = "",
) -> dict:
    ws_id = validate_workspace_id(workspace_id)
    artifact_id = f"art_{uuid.uuid4().hex[:16]}"
    now = now_iso()
    record = {
        "artifact_id": artifact_id,
        "workspace_id": ws_id,
        "session_id": session_id,
        "run_id": run_id,
        "module": "",
        "skill": "",
        "capability_id": "",
        "artifact_type": artifact_type or "unknown",
        "title": title or artifact_type or artifact_id,
        "summary": "",
        "description": "",
        "scope": scope,
        "sensitivity": sensitivity,
        "lifecycle": "active",
        "path": str(getattr(file_record, "path", "") or ""),
        "relative_path": str(getattr(file_record, "path", "") or ""),
        "mime_type": "text/plain",
        "file_ext": str(getattr(file_record, "file_kind", "") or "txt"),
        "size_bytes": int(getattr(file_record, "size_bytes", 0) or 0),
        "sha256": str(getattr(file_record, "sha256", "") or ""),
        "file_id": str(getattr(file_record, "file_id", "") or ""),
        "source": source,
        "created_by": str(created_by or "")[:160],
        "created_at": now,
        "updated_at": now,
        "expires_at": None,
        "metadata": dict(metadata or {}),
        "tags": [],
        "redaction_applied": True,
        "parent_artifact_id": None,
        "derived_from": [],
        "references": [],
    }
    upsert_artifact_record(ws_id, record, add_to_index=True)
    return record


def upsert_artifact_record(workspace_id: str, record: dict, *, add_to_index: bool = False) -> None:
    """Upsert metadata and optionally update the lightweight index atomically."""
    ws_id = validate_workspace_id(workspace_id)
    with FileLock(_metadata_lock_path(ws_id)):
        _upsert_record_unlocked(ws_id, record)
        if add_to_index:
            _append_index_unlocked(ws_id, str(record.get("artifact_id") or ""))


def remove_artifact_record(workspace_id: str, artifact_id: str) -> bool:
    ws_id = validate_workspace_id(workspace_id)
    with FileLock(_metadata_lock_path(ws_id)):
        path = workspace_root(ws_id) / "index" / "artifacts.jsonl"
        records = _read_records(path)
        kept = [item for item in records if item.get("artifact_id") != artifact_id]
        found = len(kept) != len(records)
        if found:
            _write_records(path, kept)
        index = _read_index(ws_id)
        ids = [item for item in list(index.get("artifact_ids") or []) if item != artifact_id]
        index.update({
            "workspace_id": ws_id,
            "artifact_ids": ids,
            "artifact_count": len(ids),
            "updated_at": now_iso(),
        })
        atomic_write_json(workspace_root(ws_id) / "sys" / "artifacts.index.json", index)
        return found


def list_artifact_records(workspace_id: str) -> list[dict]:
    """Return a consistent artifact metadata snapshot."""
    ws_id = validate_workspace_id(workspace_id)
    path = workspace_root(ws_id) / "index" / "artifacts.jsonl"
    if not path.is_file():
        return []
    with FileLock(_metadata_lock_path(ws_id)):
        return _read_records(path)


def read_artifact_index(workspace_id: str) -> dict:
    """Return the lightweight artifact index projection."""
    ws_id = validate_workspace_id(workspace_id)
    path = workspace_root(ws_id) / "sys" / "artifacts.index.json"
    if not path.is_file():
        return _read_index(ws_id)
    with FileLock(_metadata_lock_path(ws_id)):
        return _read_index(ws_id)


def _upsert_record_unlocked(workspace_id: str, record: dict) -> None:
    path = workspace_root(workspace_id) / "index" / "artifacts.jsonl"
    records = [
        data for data in _read_records(path)
        if data.get("artifact_id") != record["artifact_id"]
    ]
    records.append(record)
    _write_records(path, records)


def _append_index_unlocked(workspace_id: str, artifact_id: str) -> None:
    path = workspace_root(workspace_id) / "sys" / "artifacts.index.json"
    data = _read_index(workspace_id)
    ids = list(data.get("artifact_ids") or [])
    if artifact_id not in ids:
        ids.append(artifact_id)
    data.update({
        "workspace_id": workspace_id,
        "artifact_ids": ids,
        "artifact_count": len(ids),
        "updated_at": now_iso(),
    })
    atomic_write_json(path, data)


def _read_records(path) -> list[dict]:
    records: list[dict] = []
    if path.is_file():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed artifact metadata at line {line_number}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"non-object artifact metadata at line {line_number}")
            records.append(data)
    return records


def _write_records(path, records) -> None:
    atomic_write_text(
        path,
        "\n".join(json.dumps(item, ensure_ascii=False, default=str) for item in records)
        + ("\n" if records else ""),
    )


def _read_index(workspace_id: str) -> dict:
    path = workspace_root(workspace_id) / "sys" / "artifacts.index.json"
    data = {"workspace_id": workspace_id, "artifact_ids": [], "artifact_count": 0, "updated_at": ""}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed artifact index") from exc
    return data


def _metadata_lock_path(workspace_id: str):
    return workspace_root(workspace_id) / "index" / "artifacts.lock"
