"""Persistent tool execution history store."""

from __future__ import annotations

from typing import Any

from storage.atomic_io import atomic_write_json, safe_read_json
from storage.ids import validate_workspace_id
from storage.records import runtime_record_file


def save_history(workspace_id: str, entries: list[dict[str, Any]]) -> None:
    atomic_write_json(_history_path(workspace_id), entries, indent=2)


def load_history(workspace_id: str) -> list[dict[str, Any]]:
    items = safe_read_json(_history_path(workspace_id), default=[]) or []
    return items if isinstance(items, list) else []


def _history_path(workspace_id: str):
    safe_ws = validate_workspace_id(workspace_id)
    return runtime_record_file("tool_history", f"{safe_ws}.json")
