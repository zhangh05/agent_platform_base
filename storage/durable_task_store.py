"""Durable task, event, and checkpoint record repository."""

from __future__ import annotations

from typing import Any

from storage.ids import validate_checkpoint_id, validate_task_id
from storage.records import append_jsonl, atomic_save_json, list_json_records, read_jsonl, read_json_record


def save_task_record(workspace_id: str, task_id: str, record: dict[str, Any]) -> None:
    atomic_save_json(workspace_id, _task_parts(task_id), record)


def read_task_record(workspace_id: str, task_id: str) -> dict[str, Any] | None:
    return read_json_record(workspace_id, _task_parts(task_id))


def list_task_records(workspace_id: str, limit: int) -> list[dict[str, Any]]:
    return list_json_records(workspace_id, ("durable", "tasks"), limit=limit)


def append_task_event(workspace_id: str, task_id: str, record: dict[str, Any]) -> None:
    append_jsonl(workspace_id, _event_parts(task_id), record)


def list_task_events(workspace_id: str, task_id: str, limit: int) -> list[dict[str, Any]]:
    return read_jsonl(workspace_id, _event_parts(task_id))[-max(1, int(limit or 100)):]


def save_checkpoint_record(
    workspace_id: str,
    task_id: str,
    checkpoint_id: str,
    record: dict[str, Any],
) -> None:
    atomic_save_json(
        workspace_id,
        (*_checkpoint_parts(task_id), f"{validate_checkpoint_id(checkpoint_id)}.json"),
        record,
    )


def list_checkpoint_records(workspace_id: str, task_id: str) -> list[dict[str, Any]]:
    return list(reversed(list_json_records(
        workspace_id,
        _checkpoint_parts(task_id),
        limit=5000,
        sort_key=lambda item: str(item.get("created_at") or ""),
    )))


def _task_parts(task_id: str):
    return ("durable", "tasks", f"{validate_task_id(task_id)}.json")


def _event_parts(task_id: str):
    return ("durable", "events", f"{validate_task_id(task_id)}.events.json")


def _checkpoint_parts(task_id: str):
    return ("durable", "checkpoints", validate_task_id(task_id))
