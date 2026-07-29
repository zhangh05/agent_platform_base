"""Delivery rollback-plan repository."""

from __future__ import annotations

from typing import Any

from storage.records import atomic_save_json, read_json_record


def save_rollback_plan(workspace_id: str, rollback_id: str, record: dict[str, Any]) -> None:
    atomic_save_json(workspace_id, ("delivery", "rollback", f"{rollback_id}.json"), record)


def read_rollback_plan(workspace_id: str, rollback_id: str) -> dict[str, Any] | None:
    return read_json_record(workspace_id, ("delivery", "rollback", f"{rollback_id}.json"))
