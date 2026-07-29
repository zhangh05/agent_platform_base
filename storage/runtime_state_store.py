"""Global runtime state records."""

from __future__ import annotations

from typing import Any

from storage.records import atomic_save_json_path, delete_record_path, read_json_record_path, runtime_record_file


def save_runtime_record(name: str, value: dict[str, Any]) -> None:
    atomic_save_json_path(_runtime_path(name, create_parent=True), value)


def read_runtime_record(name: str) -> dict[str, Any] | None:
    return read_json_record_path(_runtime_path(name, create_parent=False))


def delete_runtime_record(name: str) -> bool:
    return delete_record_path(_runtime_path(name, create_parent=False))


def job_worker_lock_path():
    return runtime_record_file("jobs", "worker.lock")


def _runtime_path(name: str, *, create_parent: bool):
    return runtime_record_file(f"{_safe_name(name)}.json", create_parent=create_parent)


def _safe_name(name: str) -> str:
    text = str(name or "").strip()
    if not text or "/" in text or "\\" in text or ".." in text:
        raise ValueError("invalid runtime record name")
    return text
