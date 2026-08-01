"""Object storage abstraction with a safe local default."""

from __future__ import annotations

import hashlib
from pathlib import Path

from storage.atomic_io import atomic_write_bytes
from storage.paths import runtime_root


class LocalObjectStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else runtime_root() / "objects"

    def _path(self, key: str) -> Path:
        parts = str(key).split("/")
        if any(part == ".." for part in parts):
            raise ValueError("object key escapes storage root")
        clean = "/".join(part for part in parts if part not in ("", "."))
        if not clean:
            raise ValueError("object key is required")
        path = (self.root / clean).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("object key escapes storage root")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        atomic_write_bytes(self._path(key), bytes(data))
        return f"local://{key}"

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def content_hash(self, key: str) -> str | None:
        data = self.get(key)
        return hashlib.sha256(data).hexdigest() if data is not None else None


def get_object_store() -> LocalObjectStore:
    return LocalObjectStore()
