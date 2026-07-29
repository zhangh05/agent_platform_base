"""Domain repository for durable memory experience journals.

The runtime owns experience semantics; this store owns the physical JSON/JSONL
layout, cursors, and deletion.  Control-plane code must not call the generic
record adapter directly.
"""

from __future__ import annotations

from typing import Any

from storage.ids import validate_session_id, validate_workspace_id
from storage.records import (
    append_jsonl,
    atomic_save_json,
    delete_record_path,
    read_json_record,
    read_jsonl,
    workspace_record_file,
)


def append_event(workspace_id: str, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    ws_id = validate_workspace_id(workspace_id)
    sid = validate_session_id(session_id)
    return append_jsonl(ws_id, _journal_parts(sid), event)


def read_events(workspace_id: str, session_id: str) -> list[dict[str, Any]]:
    ws_id = validate_workspace_id(workspace_id)
    sid = validate_session_id(session_id)
    return read_jsonl(ws_id, _journal_parts(sid))


def read_cursor(workspace_id: str, session_id: str) -> dict[str, Any]:
    ws_id = validate_workspace_id(workspace_id)
    sid = validate_session_id(session_id)
    return read_json_record(ws_id, _cursor_parts(sid)) or {}


def save_cursor(workspace_id: str, session_id: str, cursor: dict[str, Any]) -> None:
    ws_id = validate_workspace_id(workspace_id)
    sid = validate_session_id(session_id)
    atomic_save_json(ws_id, _cursor_parts(sid), cursor)


def delete_journal(workspace_id: str, session_id: str) -> None:
    ws_id = validate_workspace_id(workspace_id)
    sid = validate_session_id(session_id)
    for parts in (_journal_parts(sid), _cursor_parts(sid)):
        path = workspace_record_file(ws_id, *parts, create_parent=False)
        delete_record_path(path)


def _journal_parts(session_id: str) -> tuple[str, ...]:
    return ("memory", "experiences", f"{session_id}.jsonl")


def _cursor_parts(session_id: str) -> tuple[str, ...]:
    return ("memory", "reflection", f"{session_id}.json")
