"""Workspace-scoped record persistence helpers.

Business modules should not need to know where JSON/JSONL records live under a
workspace. This module is the filesystem-backed adapter for small structured
records; callers pass logical store names and receive plain dicts.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from storage import paths as storage_paths
from storage.atomic_io import atomic_write_json, atomic_write_text
from storage.ids import validate_workspace_id
from storage.locking import FileLock

_LOCKS: dict[str, threading.RLock] = {}
_GLOBAL_LOCK = threading.RLock()
_FILE_LOCK_TIMEOUT_S = 5.0
_FILE_LOCK_RETRY_INTERVAL_S = 0.05


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _GLOBAL_LOCK:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def workspace_record_dir(workspace_id: str, *parts: str, create: bool = True) -> Path:
    ws = validate_workspace_id(workspace_id)
    if not parts:
        raise ValueError("record directory requires at least one path part")
    safe_parts = [_safe_part(part, allow_ext=False) for part in parts]
    path = storage_paths.workspace_root(ws).joinpath(*safe_parts)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_record_file(workspace_id: str, *parts: str, create_parent: bool = True) -> Path:
    if not parts:
        raise ValueError("record file requires at least one path part")
    parent = workspace_record_dir(workspace_id, *parts[:-1], create=create_parent)
    return parent / _safe_part(parts[-1], allow_ext=True)


def runtime_record_dir(*parts: str, create: bool = True) -> Path:
    """Return a storage-owned runtime record directory."""
    if not parts:
        raise ValueError("runtime record directory requires at least one path part")
    safe_parts = [_safe_part(part, allow_ext=False) for part in parts]
    path = storage_paths.runtime_root().joinpath(*safe_parts)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_record_file(*parts: str, create_parent: bool = True) -> Path:
    """Return a storage-owned runtime record file."""
    if not parts:
        raise ValueError("runtime record file requires at least one path part")
    if len(parts) == 1:
        parent = storage_paths.runtime_root()
        if create_parent:
            parent.mkdir(parents=True, exist_ok=True)
    else:
        parent = runtime_record_dir(*parts[:-1], create=create_parent)
    return parent / _safe_part(parts[-1], allow_ext=True)


@contextmanager
def jsonl_transaction(workspace_id: str, parts: Iterable[str]):
    """Hold the adapter lock for a JSONL record file."""
    path = workspace_record_file(workspace_id, *tuple(parts))
    lock = _lock_for(path)
    with lock:
        with _file_lock(path):
            yield


def append_jsonl(workspace_id: str, parts: Iterable[str], record: dict[str, Any]) -> dict[str, Any]:
    path = workspace_record_file(workspace_id, *tuple(parts))
    payload = dict(record)
    with _lock_for(path):
        with _file_lock(path):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return payload


def append_jsonl_path(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append one JSONL record to an explicit storage-owned path."""
    payload = dict(record)
    with _lock_for(path):
        with _file_lock(path):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return payload


def read_jsonl(workspace_id: str, parts: Iterable[str]) -> list[dict[str, Any]]:
    path = workspace_record_file(workspace_id, *tuple(parts), create_parent=False)
    if not path.exists():
        return []
    with _lock_for(path), _file_lock(path):
        raw = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def read_jsonl_path(path: Path) -> list[dict[str, Any]]:
    """Read JSONL records from an explicit storage-owned path."""
    if not path.exists():
        return []
    with _lock_for(path), _file_lock(path):
        raw = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def rewrite_jsonl(
    workspace_id: str,
    parts: Iterable[str],
    rows: Iterable[dict[str, Any] | str],
) -> None:
    path = workspace_record_file(workspace_id, *tuple(parts))
    lines: list[str] = []
    for row in rows:
        if isinstance(row, str):
            if row.strip():
                lines.append(row)
        else:
            lines.append(json.dumps(dict(row), ensure_ascii=False, default=str))
    with _lock_for(path), _file_lock(path):
        atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def rewrite_jsonl_path(path: Path, rows: Iterable[dict[str, Any] | str]) -> None:
    """Rewrite JSONL records at an explicit storage-owned path."""
    lines: list[str] = []
    for row in rows:
        if isinstance(row, str):
            if row.strip():
                lines.append(row)
        else:
            lines.append(json.dumps(dict(row), ensure_ascii=False, default=str))
    with _lock_for(path), _file_lock(path):
        atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def mutate_jsonl(
    workspace_id: str,
    parts: Iterable[str],
    mutator: Callable[[list[dict[str, Any]]], tuple[Iterable[dict[str, Any] | str], Any]],
) -> Any:
    """Atomically perform one JSONL read-modify-write transaction."""
    path = workspace_record_file(workspace_id, *tuple(parts))
    return mutate_jsonl_path(path, mutator)


def mutate_jsonl_path(
    path: Path,
    mutator: Callable[[list[dict[str, Any]]], tuple[Iterable[dict[str, Any] | str], Any]],
) -> Any:
    """Atomically mutate a JSONL file at an explicit storage-owned path."""
    path = Path(path)
    with _lock_for(path), _file_lock(path):
        rows = _read_jsonl_unlocked(path, strict=True)
        replacement, result = mutator(rows)
        _write_jsonl_unlocked(path, replacement)
        return result


def atomic_save_json(workspace_id: str, parts: Iterable[str], value: Any) -> dict[str, Any]:
    path = workspace_record_file(workspace_id, *tuple(parts))
    payload = asdict(value) if is_dataclass(value) else dict(value)
    with _lock_for(path), _file_lock(path):
        atomic_write_json(path, payload)
    return payload


def atomic_save_json_path(path: Path, value: Any) -> dict[str, Any]:
    """Atomically save a dict-like JSON record at an explicit storage-owned path."""
    payload = asdict(value) if is_dataclass(value) else dict(value)
    with _lock_for(path), _file_lock(path):
        atomic_write_json(path, payload)
    return payload


def read_json_record(workspace_id: str, parts: Iterable[str]) -> dict[str, Any] | None:
    path = workspace_record_file(workspace_id, *tuple(parts), create_parent=False)
    if not path.is_file():
        return None
    try:
        with _lock_for(path), _file_lock(path):
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_json_record_path(path: Path) -> dict[str, Any] | None:
    """Read a dict JSON record from an explicit storage-owned path."""
    if not path.is_file():
        return None
    try:
        with _lock_for(path), _file_lock(path):
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def delete_record_path(path: Path) -> bool:
    """Delete an explicit storage-owned record path."""
    if not path.is_file():
        return False
    with _lock_for(path), _file_lock(path):
        if not path.is_file():
            return False
        path.unlink()
    return True


def list_json_records(
    workspace_id: str,
    parts: Iterable[str],
    *,
    limit: int = 100,
    sort_key: Callable[[dict[str, Any]], str] | None = None,
) -> list[dict[str, Any]]:
    directory = workspace_record_dir(workspace_id, *tuple(parts), create=False)
    if not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            with _lock_for(path), _file_lock(path):
                data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            records.append(data)
    key = sort_key or (lambda item: str(item.get("updated_at") or item.get("created_at") or ""))
    records.sort(key=key, reverse=True)
    return records[: max(1, min(int(limit or 100), 5000))]


def delete_json_record(workspace_id: str, parts: Iterable[str]) -> bool:
    path = workspace_record_file(workspace_id, *tuple(parts), create_parent=False)
    if not path.is_file():
        return False
    with _lock_for(path), _file_lock(path):
        if not path.is_file():
            return False
        path.unlink()
    return True


def clear_json_record_dir(workspace_id: str, parts: Iterable[str]) -> int:
    directory = workspace_record_dir(workspace_id, *tuple(parts), create=False)
    if not directory.is_dir():
        return 0
    count = 0
    with _lock_for(directory), _file_lock(directory / ".records"):
        for path in directory.glob("*.json"):
            if not path.is_file():
                continue
            path.unlink()
            count += 1
    return count


def _safe_part(part: str, *, allow_ext: bool) -> str:
    text = str(part or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError("invalid record path part")
    if not allow_ext and "." in text:
        raise ValueError("invalid record directory part")
    return text


def _read_jsonl_unlocked(path: Path, *, strict: bool = False) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            if strict:
                raise ValueError(f"malformed_jsonl_record: {path.name}") from exc
            continue
        if isinstance(data, dict):
            rows.append(data)
        elif strict:
            raise ValueError(f"non_object_jsonl_record: {path.name}")
    return rows


def _write_jsonl_unlocked(path: Path, rows: Iterable[dict[str, Any] | str]) -> None:
    lines: list[str] = []
    for row in rows:
        if isinstance(row, str):
            if row.strip():
                lines.append(row)
        else:
            lines.append(json.dumps(dict(row), ensure_ascii=False, default=str))
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


@contextmanager
def _file_lock(path: Path):
    lock_path = Path(path).with_name(Path(path).name + ".lock")
    with FileLock(
        lock_path,
        timeout=_FILE_LOCK_TIMEOUT_S,
        retry_interval=_FILE_LOCK_RETRY_INTERVAL_S,
    ):
        yield
