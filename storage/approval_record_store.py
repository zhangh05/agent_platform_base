"""Storage-owned approval audit log records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.records import (
    append_jsonl_path,
    delete_record_path,
    mutate_jsonl_path,
    read_jsonl_path,
    workspace_record_file,
)


def approval_log_path(workspace_id: str = "") -> Path:
    """Return the approval audit log for one user workspace.

    The empty form is retained only for direct unit tests that construct an
    isolated store themselves. Production callers must supply workspace_id.
    """
    if workspace_id:
        return workspace_record_file(workspace_id, "approvals", "tool_approvals.jsonl")
    from storage.records import user_runtime_record_file
    return user_runtime_record_file("approvals", "tool_approvals.jsonl")


def append_approval_record(record: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    return append_jsonl_path(path or approval_log_path(), record)


def append_approval_records(
    records: list[dict[str, Any]], *, path: Path | None = None
) -> list[dict[str, Any]]:
    """Append one approval batch as a single atomic JSONL transaction."""
    payloads = [dict(record) for record in records]
    if not payloads:
        return []

    def _append(rows):
        return [*rows, *payloads], payloads

    return mutate_jsonl_path(path or approval_log_path(), _append)


def read_approval_records(*, path: Path | None = None) -> list[dict[str, Any]]:
    return read_jsonl_path(path or approval_log_path())


def delete_approval_log(*, path: Path | None = None) -> bool:
    return delete_record_path(path or approval_log_path())


def mutate_approval_records(mutator, *, path: Path | None = None):
    return mutate_jsonl_path(path or approval_log_path(), mutator)
