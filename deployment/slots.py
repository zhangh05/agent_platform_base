"""Immutable release slots with atomic activation and rollback."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request
from typing import Any

from storage.time_utils import now_iso


class ReleaseError(RuntimeError):
    pass


def release_root(value: str | Path | None = None) -> Path:
    configured = value or os.getenv("LZCORE_RELEASE_ROOT", "")
    root = Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parent.parent.parent / ".lzcore-releases"
    root.mkdir(parents=True, exist_ok=True)
    (root / "releases").mkdir(exist_ok=True)
    return root


def _version(value: str) -> str:
    version = value.removeprefix("v")
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ReleaseError("release version must use x.y.z")
    return version


def _ignore(_directory: str, names: list[str]) -> set[str]:
    excluded = {".git", ".venv", "node_modules", "workspaces", "logs", "reports", "data", ".runtime", "__pycache__"}
    return {name for name in names if name in excluded or name.startswith(".") or name.endswith(".pyc") or Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx"}}


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "release.json"):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _source_symlink(root: Path) -> Path | None:
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False):
        ignored = _ignore(directory, dirs + files)
        for name in dirs + files:
            path = Path(directory) / name
            if name not in ignored and path.is_symlink():
                return path
        dirs[:] = [name for name in dirs if name not in ignored]
    return None


def stage_release(source: str | Path, version: str, *, root: str | Path | None = None) -> dict[str, Any]:
    version = _version(version)
    source_path = Path(source).resolve()
    if not (source_path / "agent" / "__init__.py").is_file() or not (source_path / "frontend" / "dist" / "index.html").is_file():
        raise ReleaseError("release source must contain the backend package and built frontend")
    declared = (source_path / "agent" / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in declared:
        raise ReleaseError("release source version does not match requested version")
    base = release_root(root)
    if base == source_path or source_path in base.parents:
        raise ReleaseError("release root must stay outside the release source")
    linked = _source_symlink(source_path)
    if linked is not None:
        raise ReleaseError(f"release source contains a symbolic link: {linked.relative_to(source_path)}")
    target = base / "releases" / version
    if target.exists():
        raise ReleaseError("release slot already exists")
    stage = Path(tempfile.mkdtemp(prefix=".release-stage-", dir=base / "releases"))
    payload = stage / version
    try:
        shutil.copytree(source_path, payload, ignore=_ignore)
        manifest = {"version": version, "created_at": now_iso(), "sha256": _hash_tree(payload)}
        (payload / "release.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(payload, target)
        return {**manifest, "path": str(target)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _link_target(link: Path) -> str:
    return os.readlink(link) if link.is_symlink() else ""


def _replace_link(link: Path, target: str) -> None:
    temporary = link.with_name(f".{link.name}.tmp-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    os.symlink(target, temporary)
    os.replace(temporary, link)


def activate_release(version: str, *, root: str | Path | None = None) -> dict[str, Any]:
    version = _version(version)
    base = release_root(root)
    target = base / "releases" / version
    if not (target / "release.json").is_file():
        raise ReleaseError("release slot not found")
    current = base / "current"
    previous = base / "previous"
    old_target = _link_target(current)
    if old_target:
        _replace_link(previous, old_target)
    relative = os.path.relpath(target, base)
    _replace_link(current, relative)
    return {"ok": True, "version": version, "current": str(current), "previous": old_target, "activated_at": now_iso()}


def rollback_release(*, root: str | Path | None = None) -> dict[str, Any]:
    base = release_root(root)
    current = base / "current"
    previous = base / "previous"
    target = _link_target(previous)
    if not target or not (base / target / "release.json").is_file():
        raise ReleaseError("previous release slot not found")
    old_current = _link_target(current)
    _replace_link(current, target)
    if old_current:
        _replace_link(previous, old_current)
    manifest = json.loads((base / target / "release.json").read_text(encoding="utf-8"))
    return {"ok": True, "version": manifest["version"], "current": str(current), "rolled_back_at": now_iso()}


def verify_health(url: str, timeout: float = 10.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read())
            if response.status != 200 or not payload.get("ready", payload.get("status") == "ok"):
                raise ReleaseError("release health check was not ready")
            return payload
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("release health check failed") from exc
