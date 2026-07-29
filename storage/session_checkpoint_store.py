"""Session checkpoint repository."""

from __future__ import annotations

from typing import Any

from storage.records import atomic_save_json


def save_checkpoint(workspace_id: str, session_id: str, checkpoint_id: str, value: dict[str, Any]) -> None:
    sid = _safe_id(session_id, "session")
    cid = _safe_id(checkpoint_id, "checkpoint")
    atomic_save_json(workspace_id, ("sessions", sid, "checkpoints", f"{cid}.json"), value)


def _safe_id(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text or ".." in text:
        raise ValueError(f"invalid {label} id")
    return text
