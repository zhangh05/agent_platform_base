"""Saved remote-device and terminal-log repository."""

from __future__ import annotations

from typing import Any

from storage.atomic_io import atomic_write_text
from storage.ids import validate_run_id
from storage.records import append_jsonl, mutate_jsonl, read_jsonl, workspace_record_dir

_DEVICE_PARTS = ("remote", "connections.jsonl")


def append_device(workspace_id: str, record: dict[str, Any]) -> None:
    append_jsonl(workspace_id, _DEVICE_PARTS, record)


def read_devices(workspace_id: str) -> list[dict[str, Any]]:
    return read_jsonl(workspace_id, _DEVICE_PARTS)


def delete_device(workspace_id: str, device_id: str) -> bool:
    def _remove(rows):
        kept = [record for record in rows if record.get("device_id") != device_id]
        return kept, len(kept) != len(rows)

    return bool(mutate_jsonl(workspace_id, _DEVICE_PARTS, _remove))


def save_terminal_log(workspace_id: str, session_id: str, lines: list[str]) -> None:
    safe_session_id = validate_run_id(session_id)
    path = workspace_record_dir(workspace_id, "remote", "logs") / f"{safe_session_id}.log"
    atomic_write_text(path, "\n".join(str(line) for line in lines))
