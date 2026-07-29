# storage/reference_index.py
"""Cross-reference index linking files to owner entities."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from storage.records import append_jsonl, mutate_jsonl, read_jsonl
from storage.schemas import FileReference

_REF_INDEX_PARTS = ("index", "references.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_reference(
    workspace_id: str,
    file_id: str,
    owner_type: str,
    owner_id: str,
    relation: str = "source",
    metadata: dict[str, Any] | None = None,
) -> FileReference:
    """Add a cross-reference between a file and an owner entity."""
    ref = FileReference(
        ref_id=f"ref_{uuid.uuid4().hex[:12]}",
        workspace_id=workspace_id,
        file_id=file_id,
        owner_type=owner_type,
        owner_id=owner_id,
        relation=relation,
        created_at=_now_iso(),
        metadata=metadata or {},
    )
    append_jsonl(workspace_id, _REF_INDEX_PARTS, ref.as_dict())
    return ref


def list_references_for_file(workspace_id: str, file_id: str) -> list[dict]:
    """List all references pointing to a specific file."""
    return _query_refs(workspace_id, "file_id", file_id)


def list_references_for_owner(workspace_id: str, owner_type: str, owner_id: str) -> list[dict]:
    """List all file references owned by a specific entity."""
    return [
        r for r in _query_refs(workspace_id, "owner_id", owner_id)
        if r.get("owner_type") == owner_type
    ]


def remove_reference(workspace_id: str, ref_id: str) -> bool:
    """Remove a reference by ref_id through the storage record adapter."""
    def _remove(rows):
        kept = [row for row in rows if row.get("ref_id") != ref_id]
        return kept, len(kept) != len(rows)

    return bool(mutate_jsonl(workspace_id, _REF_INDEX_PARTS, _remove))


def _query_refs(workspace_id: str, key: str, value: str) -> list[dict]:
    return [rec for rec in read_jsonl(workspace_id, _REF_INDEX_PARTS) if rec.get(key) == value]
