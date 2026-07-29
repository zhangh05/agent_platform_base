"""Atomic filesystem IO helpers for storage adapters."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional


def _unique_tmp(path: Path) -> Path:
    suffix = f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    return path.with_name(path.name + suffix)


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp(path)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, obj: Any, *, indent: Optional[int] = 2) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=indent, default=str)
    atomic_write_text(Path(path), text)


def safe_read_text(path: Path, default: str = "") -> str:
    try:
        p = Path(path)
        if not p.is_file():
            return default
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default


def safe_read_json(path: Path, default: Any = None) -> Any:
    try:
        p = Path(path)
        if not p.is_file():
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default
