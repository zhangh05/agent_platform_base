"""Small private repository for verified extension packages."""

from __future__ import annotations

from pathlib import Path
import hashlib
import os
import shutil
import tempfile
from typing import Any

from storage.atomic_io import atomic_write_json, safe_read_json
from storage.locking import FileLock
from storage.records import runtime_record_dir, runtime_record_file
from storage.time_utils import now_iso

from .package import ExtensionPackageError, verify_package


def _index_path() -> Path:
    return runtime_record_file("extensions", "repository.json", create_parent=True)


def _packages_root() -> Path:
    return runtime_record_dir("extensions", "packages", create=True)


def list_packages() -> list[dict[str, Any]]:
    value = safe_read_json(_index_path(), {})
    records = list(value.values()) if isinstance(value, dict) else []
    return sorted((dict(item) for item in records if isinstance(item, dict)), key=lambda item: (item.get("extension_id", ""), item.get("version", "")))


def publish_package(package: str | Path, *, key: str | bytes | None = None) -> dict[str, Any]:
    metadata = verify_package(package, key=key)
    extension_id = str(metadata["extension_id"])
    version = str(metadata["version"])
    destination = _packages_root() / f"{extension_id.replace('.', '_')}-{version}.apx"
    package_digest = hashlib.sha256(Path(package).read_bytes()).hexdigest()
    record = {
        "extension_id": extension_id,
        "version": version,
        "created_at": metadata.get("created_at", ""),
        "published_at": now_iso(),
        "algorithm": metadata.get("algorithm", ""),
        "key_id": metadata.get("key_id", ""),
        "signature": metadata.get("signature", ""),
        "sha256": package_digest,
        "package_path": str(destination),
    }
    path = _index_path()
    with FileLock(path.with_name("repository.lock")):
        index = safe_read_json(path, {})
        if not isinstance(index, dict):
            index = {}
        record_key = f"{extension_id}@{version}"
        current = index.get(record_key)
        if isinstance(current, dict) and current.get("sha256") != package_digest:
            raise ExtensionPackageError("published extension versions are immutable")
        fd, temporary_name = tempfile.mkstemp(prefix=".package-", suffix=".tmp", dir=destination.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(package, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        index[record_key] = record
        atomic_write_json(path, index)
    return record


def get_package(extension_id: str, version: str) -> dict[str, Any] | None:
    return next((item for item in list_packages() if item.get("extension_id") == extension_id and item.get("version") == version), None)
