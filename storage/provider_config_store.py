"""Storage helpers for local LLM provider configuration files."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from storage.atomic_io import atomic_write_json, atomic_write_text, safe_read_json, safe_read_text
from storage.locking import FileLock


def ensure_provider_dir(providers_dir: Path) -> Path:
    providers_dir.mkdir(parents=True, exist_ok=True)
    return providers_dir


def provider_config_path(providers_dir: Path, provider_id: str) -> Path:
    return ensure_provider_dir(providers_dir) / f"{provider_id}.json"


def active_provider_path(providers_dir: Path) -> Path:
    return ensure_provider_dir(providers_dir) / "_active"


def read_provider_config(providers_dir: Path, provider_id: str) -> dict[str, Any] | None:
    data = safe_read_json(provider_config_path(providers_dir, provider_id), default=None)
    return data if isinstance(data, dict) else None


def write_provider_config(providers_dir: Path, provider_id: str, data: dict[str, Any]) -> None:
    path = provider_config_path(providers_dir, provider_id)
    with FileLock(path.with_name(path.name + ".lock")):
        atomic_write_json(path, data)
    try:
        os.chmod(str(path), stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def read_active_provider(providers_dir: Path) -> str:
    return safe_read_text(active_provider_path(providers_dir), default="").strip()


def write_active_provider(providers_dir: Path, provider_id: str) -> None:
    path = active_provider_path(providers_dir)
    with FileLock(path.with_name(path.name + ".lock")):
        atomic_write_text(path, provider_id)


def delete_provider_config(providers_dir: Path, provider_id: str) -> bool:
    path = provider_config_path(providers_dir, provider_id)
    if not path.is_file():
        return False
    with FileLock(path.with_name(path.name + ".lock")):
        if not path.is_file():
            return False
        path.unlink()
    return True
