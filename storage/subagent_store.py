"""Persisted subagent task repository."""

from __future__ import annotations

from typing import Any

from storage.records import atomic_save_json, list_json_records, read_json_record


def save_subagent(workspace_id: str, subtask_id: str, record: dict[str, Any]) -> None:
    atomic_save_json(workspace_id, ("subagents", f"{subtask_id}.json"), record)


def read_subagent(workspace_id: str, subtask_id: str) -> dict[str, Any] | None:
    return read_json_record(workspace_id, ("subagents", f"{subtask_id}.json"))


def list_subagents(workspace_id: str, limit: int) -> list[dict[str, Any]]:
    return list_json_records(workspace_id, ("subagents",), limit=limit)
