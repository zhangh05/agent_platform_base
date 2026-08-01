"""Encrypted local secret adapter for single-node and bootstrap deployments."""

from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet
from storage.atomic_io import atomic_write_json
from storage.locking import FileLock
from storage.records import runtime_record_file


def _fernet() -> Fernet:
    master = os.environ.get("AGENT_PLATFORM_MASTER_KEY", "")
    if len(master) < 16:
        raise RuntimeError("AGENT_PLATFORM_MASTER_KEY must contain at least 16 characters")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(master.encode()).digest()))


def _path():
    return runtime_record_file("secrets", "encrypted.json", create_parent=True)


def set_secret(secret_id: str, value: str) -> str:
    path = _path()
    with FileLock(path.with_name("encrypted.lock")):
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            data = {}
        data[secret_id] = _fernet().encrypt(value.encode()).decode()
        atomic_write_json(path, data)
    return f"secret://{secret_id}"


def get_secret(reference: str) -> str:
    secret_id = str(reference).removeprefix("secret://")
    path = _path()
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        encrypted = data.get(secret_id, "")
        return _fernet().decrypt(encrypted.encode()).decode() if encrypted else ""
    except Exception:
        return ""


def delete_secret(reference: str) -> bool:
    secret_id = str(reference).removeprefix("secret://")
    path = _path()
    with FileLock(path.with_name("encrypted.lock")):
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            data = {}
        existed = secret_id in data
        if existed:
            data.pop(secret_id, None)
            atomic_write_json(path, data)
        return existed
