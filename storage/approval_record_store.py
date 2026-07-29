"""Storage-owned approval audit log records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.records import (
    append_jsonl_path,
    delete_record_path,
    mutate_jsonl_path,
    read_jsonl_path,
    runtime_record_file,
)


def approval_log_path() -> Path:
    return runtime_record_file("approvals", "tool_approvals.jsonl")


def append_approval_record(record: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    return append_jsonl_path(path or approval_log_path(), record)


def read_approval_records(*, path: Path | None = None) -> list[dict[str, Any]]:
    return read_jsonl_path(path or approval_log_path())


def delete_approval_log(*, path: Path | None = None) -> bool:
    return delete_record_path(path or approval_log_path())


def mutate_approval_records(mutator, *, path: Path | None = None):
    return mutate_jsonl_path(path or approval_log_path(), mutator)
