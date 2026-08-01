"""Signed extension packages with safe, recoverable installation."""

from __future__ import annotations

import hashlib
import base64
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import secrets
import tempfile
from typing import Any
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from storage.time_utils import now_iso

from .manifest import ExtensionManifest, ExtensionValidationError
from .registry import ExtensionRegistry


PACKAGE_FORMAT = 1
MAX_PACKAGE_FILES = 5000
MAX_PACKAGE_BYTES = 100 * 1024 * 1024


class ExtensionPackageError(ValueError):
    pass


def _material(value: str | bytes | None, env_name: str) -> bytes:
    raw = value if value is not None else os.getenv(env_name, "")
    if isinstance(raw, bytes):
        return raw
    candidate = Path(raw).expanduser() if raw and "\n" not in raw else None
    if candidate and candidate.is_file():
        return candidate.read_bytes()
    return raw.encode("utf-8")


def _private_key(value: str | bytes | None = None) -> Ed25519PrivateKey:
    raw = _material(value, "AGENT_PLATFORM_EXTENSION_SIGNING_PRIVATE_KEY")
    try:
        if raw.startswith(b"-----BEGIN"):
            key = serialization.load_pem_private_key(raw, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise TypeError
            return key
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw, validate=True))
    except Exception as exc:
        raise ExtensionPackageError("a valid Ed25519 signing private key is required") from exc


def _public_key(value: str | bytes | None = None) -> Ed25519PublicKey:
    raw = _material(value, "AGENT_PLATFORM_EXTENSION_SIGNING_PUBLIC_KEY")
    try:
        if raw.startswith(b"-----BEGIN"):
            try:
                key = serialization.load_pem_public_key(raw)
            except ValueError:
                private = serialization.load_pem_private_key(raw, password=None)
                key = private.public_key()
            if not isinstance(key, Ed25519PublicKey):
                raise TypeError
            return key
        decoded = base64.b64decode(raw, validate=True)
        if len(decoded) == 32:
            return Ed25519PublicKey.from_public_bytes(decoded)
        return Ed25519PrivateKey.from_private_bytes(decoded).public_key()
    except Exception as exc:
        raise ExtensionPackageError("a valid Ed25519 verification public key is required") from exc


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_name(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ExtensionPackageError("package path must use forward slashes")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ExtensionPackageError(f"unsafe package path: {name}")
    return path


def _source_files(source: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in {"__pycache__", ".pytest_cache", "node_modules"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ExtensionPackageError(f"symbolic links are not allowed: {relative}")
        if path.is_file():
            if any(part.startswith(".") for part in relative.parts) or path.name.lower() in {"env", ".env"} or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
                raise ExtensionPackageError(f"secret or hidden files cannot be packaged: {relative}")
            files.append(path)
    if len(files) > MAX_PACKAGE_FILES:
        raise ExtensionPackageError("extension package contains too many files")
    return files


def build_package(source: str | Path, output: str | Path, *, key: str | bytes | None = None) -> dict[str, Any]:
    source_path = Path(source).resolve()
    manifest_path = source_path / "extension.json"
    if not manifest_path.is_file():
        raise ExtensionPackageError("extension.json is required")
    manifest = ExtensionRegistry.load(manifest_path)
    file_payloads: dict[str, bytes] = {}
    total = 0
    for path in _source_files(source_path):
        relative = path.relative_to(source_path).as_posix()
        _safe_name(relative)
        payload = path.read_bytes()
        total += len(payload)
        if total > MAX_PACKAGE_BYTES:
            raise ExtensionPackageError("extension package is too large")
        file_payloads[relative] = payload
    files = {name: hashlib.sha256(payload).hexdigest() for name, payload in file_payloads.items()}
    private_key = _private_key(key)
    public_bytes = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    signed = {
        "format": PACKAGE_FORMAT,
        "extension_id": manifest.extension_id,
        "version": manifest.version,
        "created_at": now_iso(),
        "algorithm": "ed25519",
        "key_id": hashlib.sha256(public_bytes).hexdigest()[:16],
        "files": files,
    }
    metadata = dict(signed)
    metadata["signature"] = base64.b64encode(private_key.sign(_canonical(signed))).decode("ascii")
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("package.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        for name, payload in file_payloads.items():
            archive.writestr(f"extension/{name}", payload)
    return metadata


def verify_package(package: str | Path, *, key: str | bytes | None = None) -> dict[str, Any]:
    package_path = Path(package)
    try:
        archive = zipfile.ZipFile(package_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExtensionPackageError("invalid extension package") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_FILES + 1:
            raise ExtensionPackageError("extension package contains too many files")
        total = 0
        names: set[str] = set()
        for info in infos:
            path = _safe_name(info.filename)
            if info.filename in names:
                raise ExtensionPackageError(f"duplicate package path: {info.filename}")
            names.add(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ExtensionPackageError("symbolic links are not allowed")
            total += info.file_size
            if total > MAX_PACKAGE_BYTES:
                raise ExtensionPackageError("extension package is too large")
            if path.parts[0] not in {"package.json", "extension"}:
                raise ExtensionPackageError(f"unexpected package path: {info.filename}")
        if "package.json" not in names:
            raise ExtensionPackageError("package.json is required")
        try:
            metadata = json.loads(archive.read("package.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ExtensionPackageError("invalid package metadata") from exc
        if not isinstance(metadata, dict) or metadata.get("format") != PACKAGE_FORMAT:
            raise ExtensionPackageError("unsupported extension package format")
        signature = str(metadata.pop("signature", ""))
        if metadata.get("algorithm") != "ed25519":
            raise ExtensionPackageError("unsupported extension signature algorithm")
        try:
            public_key = _public_key(key)
            public_bytes = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            if metadata.get("key_id") != hashlib.sha256(public_bytes).hexdigest()[:16]:
                raise ValueError("key id mismatch")
            public_key.verify(base64.b64decode(signature, validate=True), _canonical(metadata))
        except Exception:
            raise ExtensionPackageError("extension package signature mismatch")
        declared = metadata.get("files")
        if not isinstance(declared, dict) or "extension.json" not in declared:
            raise ExtensionPackageError("invalid extension file index")
        actual_names = {name.removeprefix("extension/") for name in names if name.startswith("extension/") and not name.endswith("/")}
        if actual_names != set(declared):
            raise ExtensionPackageError("extension package file index mismatch")
        for name, digest in declared.items():
            _safe_name(str(name))
            payload = archive.read(f"extension/{name}")
            if not secrets.compare_digest(hashlib.sha256(payload).hexdigest(), str(digest)):
                raise ExtensionPackageError(f"extension file checksum mismatch: {name}")
        try:
            manifest_data = json.loads(archive.read("extension/extension.json"))
            manifest = ExtensionManifest.from_dict(manifest_data)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ExtensionValidationError) as exc:
            raise ExtensionPackageError("invalid packaged extension manifest") from exc
        if manifest.extension_id != metadata.get("extension_id") or manifest.version != metadata.get("version"):
            raise ExtensionPackageError("package metadata does not match extension manifest")
        result = dict(metadata)
        result["signature"] = signature
        result["manifest"] = manifest.to_dict()
        return result


def _plugin_root(value: str | Path | None = None) -> Path:
    return Path(value) if value is not None else Path(__file__).resolve().parent.parent / "plugins"


def _version_tuple(version: str) -> tuple[int, int, int]:
    core = version.split("-", 1)[0].split("+", 1)[0]
    return tuple(int(part) for part in core.split("."))  # type: ignore[return-value]


def install_package(package: str | Path, *, key: str | bytes | None = None, plugins_root: str | Path | None = None, upgrade: bool = False) -> dict[str, Any]:
    metadata = verify_package(package, key=key)
    root = _plugin_root(plugins_root)
    root.mkdir(parents=True, exist_ok=True)
    extension_id = str(metadata["extension_id"])
    target = root / extension_id.replace(".", "_")
    if target.exists() and not upgrade:
        raise ExtensionPackageError("extension is already installed; use upgrade")
    stage_root = Path(tempfile.mkdtemp(prefix=".extension-stage-", dir=root))
    staged = stage_root / "payload"
    staged.mkdir()
    backup: Path | None = None
    try:
        with zipfile.ZipFile(package, "r") as archive:
            for name in metadata["files"]:
                destination = staged.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(f"extension/{name}"))
        loaded = ExtensionRegistry.load(staged / "extension.json")
        if loaded.extension_id != extension_id:
            raise ExtensionPackageError("staged extension identity mismatch")
        if target.exists():
            current = ExtensionRegistry.load(target / "extension.json")
            if current.extension_id != extension_id:
                raise ExtensionPackageError("installed extension identity mismatch")
            if _version_tuple(str(metadata["version"])) <= _version_tuple(current.version):
                raise ExtensionPackageError("upgrade version must be newer than the installed version")
            backup_root = root / ".extension-backups"
            backup_root.mkdir(exist_ok=True)
            backup = backup_root / f"{target.name}-{current.version}-{now_iso().replace(':', '').replace('+', '_')}"
            os.replace(target, backup)
        try:
            os.replace(staged, target)
        except Exception:
            if backup and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
    return {"ok": True, "extension_id": extension_id, "version": metadata["version"], "path": str(target), "backup": str(backup) if backup else ""}


def uninstall_extension(extension_id: str, *, plugins_root: str | Path | None = None) -> dict[str, Any]:
    root = _plugin_root(plugins_root)
    target = root / extension_id.replace(".", "_")
    if not target.is_dir():
        raise ExtensionPackageError("installed plugin extension not found")
    manifest = ExtensionRegistry.load(target / "extension.json")
    if manifest.extension_id != extension_id:
        raise ExtensionPackageError("installed extension identity mismatch")
    trash_root = root / ".extension-trash"
    trash_root.mkdir(exist_ok=True)
    destination = trash_root / f"{target.name}-{manifest.version}-{now_iso().replace(':', '').replace('+', '_')}"
    os.replace(target, destination)
    return {"ok": True, "extension_id": extension_id, "version": manifest.version, "recoverable_path": str(destination)}
