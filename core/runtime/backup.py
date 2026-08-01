"""Verified filesystem snapshots with atomic, recoverable restore."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time
import uuid
from typing import Any

from storage.locking import FileLock
from storage.paths import get_workspace_root
from storage.time_utils import now_iso


class BackupError(RuntimeError):
    pass


MAX_BACKUP_FILES = 100_000
MAX_BACKUP_BYTES = 10 * 1024 * 1024 * 1024


def backup_root() -> Path:
    configured = os.getenv("AGENT_PLATFORM_BACKUP_DIR", "").strip()
    root = Path(configured).expanduser().resolve() if configured else get_workspace_root().parent / ".agent-platform-backups"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _data_root() -> Path:
    root = get_workspace_root().resolve()
    if root == Path(root.anchor) or len(root.parts) < 3:
        raise BackupError("unsafe workspace root")
    return root


def _files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    result = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise BackupError(f"workspace snapshot contains a symbolic link: {relative}")
        if path.is_file() and not path.name.endswith(".lock") and ".tmp." not in path.name:
            result.append(path)
    return result


def create_backup(*, retries: int = 3) -> dict[str, Any]:
    root = _data_root()
    destination_root = backup_root()
    backup_id = f"backup-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    destination = destination_root / f"{backup_id}.tar.gz"
    with FileLock(destination_root / "backup.lock", timeout=30):
        for attempt in range(max(1, retries)):
            try:
                return _create_stable_snapshot(root, destination, backup_id)
            except BackupError:
                if attempt + 1 >= max(1, retries):
                    raise
                time.sleep(0.05)
    raise BackupError("backup failed")


def _create_stable_snapshot(root: Path, destination: Path, backup_id: str) -> dict[str, Any]:
    stage = Path(tempfile.mkdtemp(prefix=".backup-stage-", dir=destination.parent))
    try:
        source_files = _files(root)
        if len(source_files) > MAX_BACKUP_FILES:
            raise BackupError("workspace contains too many files for one backup")
        source_names = [path.relative_to(root).as_posix() for path in source_files]
        entries: list[dict[str, Any]] = []
        total_bytes = 0
        for source, name in zip(source_files, source_names):
            before = source.stat()
            target = stage / "data" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as input_stream, target.open("wb") as output_stream:
                while chunk := input_stream.read(1024 * 1024):
                    output_stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise BackupError(f"workspace changed during snapshot: {name}")
            entries.append({"path": name, "size": size, "sha256": digest.hexdigest()})
            total_bytes += size
            if total_bytes > MAX_BACKUP_BYTES:
                raise BackupError("workspace backup exceeds the configured safety limit")
        if source_names != [path.relative_to(root).as_posix() for path in _files(root)]:
            raise BackupError("workspace file set changed during snapshot")
        from agent import __version__
        manifest = {
            "format": 1,
            "backup_id": backup_id,
            "created_at": now_iso(),
            "platform_version": __version__,
            "file_count": len(entries),
            "total_bytes": total_bytes,
            "files": entries,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary = destination.with_suffix(".tar.gz.tmp")
        try:
            with tarfile.open(temporary, "w:gz") as archive:
                archive.add(stage / "manifest.json", arcname="manifest.json", recursive=False)
                if (stage / "data").exists():
                    archive.add(stage / "data", arcname="data")
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return {**manifest, "size_bytes": destination.stat().st_size, "path": str(destination)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _safe_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise BackupError("backup paths must use forward slashes")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupError(f"unsafe backup path: {name}")
    return path


def verify_backup(archive_path: str | Path) -> dict[str, Any]:
    try:
        archive = tarfile.open(archive_path, "r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise BackupError("invalid backup archive") from exc
    with archive:
        members = archive.getmembers()
        if len(members) > MAX_BACKUP_FILES * 2 + 2 or sum(member.size for member in members if member.isfile()) > MAX_BACKUP_BYTES:
            raise BackupError("backup archive exceeds safety limits")
        names: set[str] = set()
        for member in members:
            _safe_member(member.name)
            if member.name in names:
                raise BackupError(f"duplicate backup path: {member.name}")
            names.add(member.name)
            if member.issym() or member.islnk() or member.isdev():
                raise BackupError("backup links and device files are not allowed")
        try:
            manifest_stream = archive.extractfile("manifest.json")
            manifest = json.loads(manifest_stream.read() if manifest_stream else b"")
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BackupError("invalid backup manifest") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != 1 or not isinstance(manifest.get("files"), list):
            raise BackupError("unsupported backup format")
        declared = {str(item.get("path")): item for item in manifest["files"] if isinstance(item, dict)}
        if len(declared) != len(manifest["files"]):
            raise BackupError("backup manifest contains duplicate or invalid file entries")
        actual = {name.removeprefix("data/") for name in names if name.startswith("data/") and name != "data" and archive.getmember(name).isfile()}
        if actual != set(declared):
            raise BackupError("backup file index mismatch")
        if manifest.get("file_count") != len(declared) or int(manifest.get("total_bytes") or 0) != sum(int(item.get("size") or 0) for item in declared.values()):
            raise BackupError("backup manifest totals do not match the file index")
        for name, entry in declared.items():
            _safe_member(name)
            try:
                stream = archive.extractfile(f"data/{name}")
            except KeyError as exc:
                raise BackupError(f"backup file is missing: {name}") from exc
            digest = hashlib.sha256()
            size = 0
            if stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            if size != int(entry.get("size") or 0) or digest.hexdigest() != entry.get("sha256"):
                raise BackupError(f"backup checksum mismatch: {name}")
        return manifest


def restore_backup(archive_path: str | Path, *, confirmation: str) -> dict[str, Any]:
    if confirmation != "RESTORE":
        raise BackupError("restore confirmation is required")
    archive_hash = _file_hash(Path(archive_path))
    manifest = verify_backup(archive_path)
    root = _data_root()
    parent = root.parent
    stage = Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=parent))
    rollback = parent / f".{root.name}-before-{manifest['backup_id']}"
    try:
        data = stage / root.name
        data.mkdir()
        with tarfile.open(archive_path, "r:gz") as archive:
            for entry in manifest["files"]:
                name = str(entry["path"])
                target = data.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(f"data/{name}")
                with target.open("wb") as output_stream:
                    if stream:
                        shutil.copyfileobj(stream, output_stream, length=1024 * 1024)
        if archive_hash != _file_hash(Path(archive_path)):
            raise BackupError("backup archive changed during restore")
        with FileLock(backup_root() / "restore.lock", timeout=30):
            if rollback.exists():
                raise BackupError("restore rollback path already exists")
            if root.exists():
                os.replace(root, rollback)
            try:
                os.replace(data, root)
            except Exception:
                if rollback.exists() and not root.exists():
                    os.replace(rollback, root)
                raise
        return {"ok": True, "backup_id": manifest["backup_id"], "restored_at": now_iso(), "rollback_path": str(rollback)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def list_backups() -> list[dict[str, Any]]:
    records = []
    for path in sorted(backup_root().glob("backup-*.tar.gz"), reverse=True):
        try:
            manifest = verify_backup(path)
            records.append({key: manifest.get(key) for key in ("backup_id", "created_at", "platform_version", "file_count", "total_bytes")} | {"size_bytes": path.stat().st_size})
        except BackupError as exc:
            records.append({"backup_id": path.name.removesuffix(".tar.gz"), "status": "invalid", "error": str(exc)})
    return records


def prune_backups(keep: int = 10) -> list[str]:
    keep = max(1, min(int(keep), 100))
    paths = sorted(backup_root().glob("backup-*.tar.gz"), reverse=True)
    removed = []
    for path in paths[keep:]:
        path.unlink()
        removed.append(path.name.removesuffix(".tar.gz"))
    return removed


def backup_path(backup_id: str) -> Path:
    if not backup_id.startswith("backup-") or not all(character.isalnum() or character in {"-", "_"} for character in backup_id):
        raise BackupError("invalid backup id")
    path = backup_root() / f"{backup_id}.tar.gz"
    if not path.is_file():
        raise BackupError("backup not found")
    return path


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
