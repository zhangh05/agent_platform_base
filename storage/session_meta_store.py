"""Session metadata store."""

from __future__ import annotations

from typing import Any

from storage.records import atomic_save_json, read_json_record


def read_session_meta(workspace_id: str, session_id: str) -> dict[str, Any]:
    data = read_json_record(workspace_id, _meta_parts(session_id))
    return data if isinstance(data, dict) else {}


def save_session_meta(workspace_id: str, session_id: str, meta: dict[str, Any]) -> None:
    atomic_save_json(workspace_id, _meta_parts(session_id), dict(meta))


def _meta_parts(session_id: str) -> tuple[str, ...]:
    sid = str(session_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        raise ValueError("invalid session_id")
    return ("sessions", sid, "meta.json")
