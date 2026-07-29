"""Filesystem-backed ecosystem provider repository."""

from __future__ import annotations

from typing import Any

from storage.records import (
    atomic_save_json,
    delete_json_record,
    list_json_records,
    read_json_record,
)


def save_provider(workspace_id: str, provider_id: str, value: dict[str, Any]) -> None:
    atomic_save_json(workspace_id, _provider_parts(provider_id), value)


def get_provider(workspace_id: str, provider_id: str) -> dict[str, Any] | None:
    return read_json_record(workspace_id, _provider_parts(provider_id))


def list_providers(workspace_id: str) -> list[dict[str, Any]]:
    return list_json_records(
        workspace_id,
        ("ecosystem", "providers"),
        limit=500,
        sort_key=lambda item: str(item.get("provider_id") or ""),
    )


def delete_provider(workspace_id: str, provider_id: str) -> bool:
    return delete_json_record(workspace_id, _provider_parts(provider_id))


def _provider_parts(provider_id: str) -> tuple[str, ...]:
    pid = str(provider_id or "").strip()
    if not pid or "/" in pid or "\\" in pid or ".." in pid:
        raise ValueError("invalid ecosystem provider id")
    return ("ecosystem", "providers", f"{pid}.json")
