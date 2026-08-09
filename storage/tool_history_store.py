"""Persistent tool execution history store."""

from __future__ import annotations

from typing import Any

from storage.atomic_io import atomic_write_json, safe_read_json
from storage.ids import validate_workspace_id
from storage.records import workspace_record_file


def save_history(workspace_id: str, entries: list[dict[str, Any]]) -> None:
    atomic_write_json(history_path(workspace_id), entries, indent=2)


def load_history(workspace_id: str) -> list[dict[str, Any]]:
    items = safe_read_json(history_path(workspace_id), default=[]) or []
    return items if isinstance(items, list) else []


def history_path(workspace_id: str):
    """Return the principal-scoped physical history path for a workspace."""
    safe_ws = validate_workspace_id(workspace_id)
    return workspace_record_file(safe_ws, "runtime", "tool_history.json")
