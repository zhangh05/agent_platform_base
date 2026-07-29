"""Token usage record repository."""

from __future__ import annotations

from typing import Any

from storage.records import append_jsonl, delete_json_record, read_jsonl

_USAGE_PARTS = ("usage", "token_usage.jsonl")


def append_usage(workspace_id: str, record: dict[str, Any]) -> None:
    append_jsonl(workspace_id, _USAGE_PARTS, record)


def read_usage(workspace_id: str) -> list[dict[str, Any]]:
    return read_jsonl(workspace_id, _USAGE_PARTS)


def clear_usage(workspace_id: str) -> bool:
    return delete_json_record(workspace_id, _USAGE_PARTS)
