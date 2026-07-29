"""Manual review sidecar repository."""

from __future__ import annotations

from typing import Any

from storage.records import atomic_save_json, read_json_record


def load_sidecar(workspace_id: str, artifact_id: str) -> dict[str, Any] | None:
    return read_json_record(workspace_id, _sidecar_parts(artifact_id))


def save_sidecar(workspace_id: str, artifact_id: str, value: dict[str, Any]) -> None:
    atomic_save_json(workspace_id, _sidecar_parts(artifact_id), value)


def _sidecar_parts(artifact_id: str) -> tuple[str, ...]:
    clean = str(artifact_id or "").strip()
    if not clean or len(clean) > 128 or ".." in clean or "/" in clean or "\\" in clean:
        raise ValueError(f"invalid artifact_id: {artifact_id!r}")
    return ("sys", "reviews", f"{clean}.json")
