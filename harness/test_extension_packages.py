from __future__ import annotations

import base64
import json
from pathlib import Path
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from extensions.package import (
    ExtensionPackageError,
    build_package,
    install_package,
    uninstall_extension,
    verify_package,
)
from extensions.repository import get_package, publish_package


def _keys() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_value = base64.b64encode(private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )).decode()
    public_value = base64.b64encode(private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )).decode()
    return private_value, public_value


def _extension(root: Path, version: str = "1.0.0") -> Path:
    source = root / f"source-{version}"
    source.mkdir(parents=True)
    (source / "extension.json").write_text(json.dumps({
        "extension_id": "vendor.sample",
        "name": "Vendor Sample",
        "version": version,
        "api_version": "1",
        "capabilities": ["sample"],
    }), encoding="utf-8")
    (source / "backend.py").write_text("VALUE = 'safe'\n", encoding="utf-8")
    return source


def test_signed_package_verifies_and_rejects_wrong_key(tmp_path: Path):
    private, public = _keys()
    package = tmp_path / "sample.apx"
    metadata = build_package(_extension(tmp_path), package, key=private)
    verified = verify_package(package, key=public)
    assert verified["extension_id"] == "vendor.sample"
    assert verified["key_id"] == metadata["key_id"]
    _, wrong_public = _keys()
    with pytest.raises(ExtensionPackageError, match="signature mismatch"):
        verify_package(package, key=wrong_public)


def test_package_rejects_tampering_and_path_traversal(tmp_path: Path):
    private, public = _keys()
    package = tmp_path / "sample.apx"
    build_package(_extension(tmp_path), package, key=private)
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("../escape", b"unsafe")
    with pytest.raises(ExtensionPackageError, match="unsafe package path"):
        verify_package(package, key=public)

    clean = tmp_path / "clean.apx"
    build_package(_extension(tmp_path / "second"), clean, key=private)
    rewritten = tmp_path / "tampered.apx"
    with zipfile.ZipFile(clean, "r") as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "extension/backend.py":
                payload = b"VALUE = 'tampered'\n"
            target.writestr(info, payload)
    with pytest.raises(ExtensionPackageError, match="checksum mismatch"):
        verify_package(rewritten, key=public)


def test_install_upgrade_publish_and_soft_uninstall(monkeypatch, tmp_path: Path):
    private, public = _keys()
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    first = tmp_path / "v1.apx"
    second = tmp_path / "v2.apx"
    build_package(_extension(tmp_path / "one", "1.0.0"), first, key=private)
    build_package(_extension(tmp_path / "two", "1.1.0"), second, key=private)
    plugins = tmp_path / "plugins"

    installed = install_package(first, key=public, plugins_root=plugins)
    assert Path(installed["path"], "extension.json").is_file()
    with pytest.raises(ExtensionPackageError, match="already installed"):
        install_package(first, key=public, plugins_root=plugins)
    upgraded = install_package(second, key=public, plugins_root=plugins, upgrade=True)
    assert upgraded["version"] == "1.1.0"
    assert Path(upgraded["backup"]).is_dir()

    published = publish_package(second, key=public)
    assert get_package("vendor.sample", "1.1.0") == published
    conflicting = tmp_path / "conflicting.apx"
    conflict_source = _extension(tmp_path / "three", "1.1.0")
    (conflict_source / "backend.py").write_text("VALUE = 'different'\n", encoding="utf-8")
    build_package(conflict_source, conflicting, key=private)
    with pytest.raises(ExtensionPackageError, match="immutable"):
        publish_package(conflicting, key=public)

    from backend.main import create_app
    catalog = create_app().test_client().get("/api/extensions/repository")
    assert catalog.status_code == 200
    assert "package_path" not in catalog.get_json()["packages"][0]
    removed = uninstall_extension("vendor.sample", plugins_root=plugins)
    assert Path(removed["recoverable_path"]).is_dir()
    assert not (plugins / "vendor_sample").exists()
