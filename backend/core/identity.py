"""File-backed identity and RBAC adapter for the single-node deployment.

The data model is deliberately compatible with a future SQL adapter. Passwords
are never stored in plaintext; the existing environment-variable login remains
available when identity mode is disabled.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Any

from storage.atomic_io import atomic_write_json
from storage.records import runtime_record_file
from storage.locking import FileLock

_ROLES = {"owner", "admin", "developer", "operator", "viewer"}


def identity_enabled() -> bool:
    return os.environ.get("AGENT_PLATFORM_IDENTITY_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _path():
    return runtime_record_file("identity", "users.json", create_parent=True)


def _read() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return {"users": [], "organizations": [], "memberships": []}
    import json
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"users": [], "organizations": [], "memberships": []}
    except (OSError, ValueError):
        return {"users": [], "organizations": [], "memberships": []}


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return "pbkdf2_sha256$240000$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def _verify_password(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt_text, digest_text = encoded.split("$", 3)
        salt = base64.urlsafe_b64decode(salt_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(base64.urlsafe_b64encode(actual).decode(), digest_text)
    except (ValueError, TypeError):
        return False


def upsert_user(username: str, password: str, role: str = "viewer", organization_id: str = "default", workspace_ids: list[str] | None = None) -> dict[str, Any]:
    username = username.strip()
    if not username or not password or role not in _ROLES:
        raise ValueError("username, password and a valid role are required")
    if workspace_ids is not None and not isinstance(workspace_ids, list):
        raise ValueError("workspace_ids must be a list")
    from storage.ids import validate_workspace_id
    allowed = sorted({validate_workspace_id(item) for item in (workspace_ids or [organization_id])})
    with FileLock(_path().with_name("users.lock")):
        data = _read()
        users = [item for item in data["users"] if item.get("username") != username]
        record = {"username": username, "password_hash": _hash_password(password), "role": role, "organization_id": organization_id, "workspace_ids": allowed}
        users.append(record)
        data["users"] = users
        atomic_write_json(_path(), data)
    return {key: value for key, value in record.items() if key != "password_hash"}


def verify_user(username: str, password: str) -> dict[str, Any] | None:
    for user in _read().get("users", []):
        if user.get("username") == username and _verify_password(password, str(user.get("password_hash", ""))):
            return {key: value for key, value in user.items() if key != "password_hash"}
    return None


def get_user(username: str) -> dict[str, Any] | None:
    for user in _read().get("users", []):
        if user.get("username") == username:
            return {key: value for key, value in user.items() if key != "password_hash"}
    return None


def list_users() -> list[dict[str, Any]]:
    return [{key: value for key, value in user.items() if key != "password_hash"} for user in _read().get("users", [])]


def has_role(role: str, minimum: str) -> bool:
    order = {"viewer": 0, "operator": 1, "developer": 2, "admin": 3, "owner": 4}
    return order.get(role, -1) >= order.get(minimum, 99)


def can_access_workspace(role: str, workspace_ids: list[str], workspace_id: str, *, write: bool = False) -> bool:
    if has_role(role, "admin"):
        return True
    if write and role == "viewer":
        return False
    return workspace_id in set(workspace_ids or [])
