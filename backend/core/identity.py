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
import re
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
        if not isinstance(value, dict):
            return {"users": [], "organizations": [], "memberships": []}
        return {"users": list(value.get("users") or []), "organizations": list(value.get("organizations") or []), "memberships": list(value.get("memberships") or [])}
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


def upsert_user(username: str, password: str, role: str = "viewer", organization_id: str = "default", workspace_ids: list[str] | None = None, *, home_workspace_id: str = "") -> dict[str, Any]:
    username = username.strip()
    if not username or not password or role not in _ROLES:
        raise ValueError("username, password and a valid role are required")
    if workspace_ids is not None and not isinstance(workspace_ids, list):
        raise ValueError("workspace_ids must be a list")
    organization_id = _organization_id(organization_id)
    from storage.ids import validate_workspace_id
    home_workspace_id = validate_workspace_id(home_workspace_id) if home_workspace_id else ""
    allowed = sorted({validate_workspace_id(item) for item in (workspace_ids or [organization_id])} | ({home_workspace_id} if home_workspace_id else set()))
    with FileLock(_path().with_name("users.lock")):
        data = _read()
        existing_organization = next((item for item in data["organizations"] if item.get("organization_id") == organization_id), None)
        if existing_organization is not None and not set(allowed).issubset(set(existing_organization.get("workspace_ids") or [])):
            raise ValueError("workspace is not assigned to the organization")
        if existing_organization is None and any(set(allowed) & set(item.get("workspace_ids") or []) for item in data["organizations"]):
            raise ValueError("workspace is already assigned to another organization")
        previous = next((item for item in data["users"] if item.get("username") == username), {})
        users = [item for item in data["users"] if item.get("username") != username]
        record = {"username": username, "password_hash": _hash_password(password), "role": role, "organization_id": organization_id, "workspace_ids": allowed, "home_workspace_id": home_workspace_id or previous.get("home_workspace_id", ""), "enabled": previous.get("enabled", True) is not False}
        users.append(record)
        data["users"] = users
        if existing_organization is None:
            data["organizations"].append({"organization_id": organization_id, "name": organization_id, "workspace_ids": list(allowed)})
        memberships = [item for item in data["memberships"] if not (item.get("username") == username and item.get("organization_id") == organization_id)]
        memberships.append({"username": username, "organization_id": organization_id, "role": role, "workspace_ids": list(allowed)})
        data["memberships"] = memberships
        atomic_write_json(_path(), data)
    return _project_user(data, record)


def verify_user(username: str, password: str) -> dict[str, Any] | None:
    data = _read()
    for user in data.get("users", []):
        if user.get("username") == username and user.get("enabled", True) is not False and _verify_password(password, str(user.get("password_hash", ""))):
            return _project_user(data, user)
    return None


def get_user(username: str) -> dict[str, Any] | None:
    data = _read()
    for user in data.get("users", []):
        if user.get("username") == username:
            return _project_user(data, user)
    return None


def list_users() -> list[dict[str, Any]]:
    data = _read()
    return [_project_user(data, user) for user in data.get("users", [])]


def delete_user(username: str) -> dict[str, Any]:
    """Remove a login account and its memberships without deleting audit data."""
    username = str(username or "").strip()
    if not username:
        raise ValueError("username is required")
    with FileLock(_path().with_name("users.lock")):
        data = _read()
        user = next((item for item in data["users"] if item.get("username") == username), None)
        if user is None:
            raise ValueError("user not found")
        projected = _project_user(data, user)
        data["users"] = [item for item in data["users"] if item.get("username") != username]
        data["memberships"] = [item for item in data["memberships"] if item.get("username") != username]
        atomic_write_json(_path(), data)
        return projected


def has_role(role: str, minimum: str) -> bool:
    order = {"viewer": 0, "operator": 1, "developer": 2, "admin": 3, "owner": 4}
    return order.get(role, -1) >= order.get(minimum, 99)


def can_access_workspace(role: str, workspace_ids: list[str], workspace_id: str, *, write: bool = False) -> bool:
    if write and role == "viewer":
        return False
    return workspace_id in set(workspace_ids or [])


def _organization_id(value: str) -> str:
    organization_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", organization_id):
        raise ValueError("invalid organization_id")
    return organization_id


def _project_user(data: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    organization_id = str(user.get("organization_id") or "default")
    membership = next((item for item in data.get("memberships", []) if item.get("username") == user.get("username") and item.get("organization_id") == organization_id), {})
    role = str(membership.get("role") or user.get("role") or "viewer")
    workspace_ids = set(user.get("workspace_ids") or []) | set(membership.get("workspace_ids") or [])
    organization = next((item for item in data.get("organizations", []) if item.get("organization_id") == organization_id), {})
    if has_role(role, "admin"):
        workspace_ids.update(organization.get("workspace_ids") or [])
    return {
        "username": str(user.get("username") or ""),
        "role": role,
        "organization_id": organization_id,
        "workspace_ids": sorted(workspace_ids),
        "home_workspace_id": str(user.get("home_workspace_id") or ""),
        "enabled": user.get("enabled", True) is not False,
    }


def ensure_organization(organization_id: str, name: str, workspace_ids: list[str] | None = None) -> dict[str, Any]:
    """Create a tenant if needed and safely attach currently unowned workspaces."""
    organization_id = _organization_id(organization_id)
    from storage.ids import validate_workspace_id
    requested = {validate_workspace_id(item) for item in (workspace_ids or [])}
    with FileLock(_path().with_name("users.lock")):
        data = _read()
        organization = next((item for item in data["organizations"] if item.get("organization_id") == organization_id), None)
        if organization is None:
            organization = {"organization_id": organization_id, "name": str(name or organization_id)[:120], "workspace_ids": []}
            data["organizations"].append(organization)
        owned_elsewhere = {
            workspace_id
            for item in data["organizations"]
            if item is not organization
            for workspace_id in (item.get("workspace_ids") or [])
        }
        organization["workspace_ids"] = sorted(set(organization.get("workspace_ids") or []) | (requested - owned_elsewhere))
        atomic_write_json(_path(), data)
        return dict(organization)


def update_user_access(username: str, role: str, organization_id: str, workspace_ids: list[str] | None, *, enabled: bool = True, password: str = "") -> dict[str, Any]:
    """Update permissions without forcing an unrelated password reset."""
    username = str(username or "").strip()
    organization_id = _organization_id(organization_id)
    if not username or role not in _ROLES:
        raise ValueError("username and a valid role are required")
    if not isinstance(workspace_ids, list):
        raise ValueError("workspace_ids must be a list")
    from storage.ids import validate_workspace_id
    allowed = sorted({validate_workspace_id(item) for item in workspace_ids})
    with FileLock(_path().with_name("users.lock")):
        data = _read()
        user = next((item for item in data["users"] if item.get("username") == username), None)
        if user is None:
            raise ValueError("user not found")
        home_workspace_id = str(user.get("home_workspace_id") or "")
        if home_workspace_id:
            allowed = sorted(set(allowed) | {validate_workspace_id(home_workspace_id)})
        organization = next((item for item in data["organizations"] if item.get("organization_id") == organization_id), None)
        if organization is None:
            raise ValueError("organization not found")
        if not set(allowed).issubset(set(organization.get("workspace_ids") or [])):
            raise ValueError("workspace is not assigned to the organization")
        user.update({"role": role, "organization_id": organization_id, "workspace_ids": allowed, "enabled": bool(enabled)})
        if password:
            user["password_hash"] = _hash_password(password)
        memberships = [item for item in data["memberships"] if item.get("username") != username]
        memberships.append({"username": username, "organization_id": organization_id, "role": role, "workspace_ids": allowed})
        data["memberships"] = memberships
        atomic_write_json(_path(), data)
        return _project_user(data, user)


def create_organization(organization_id: str, name: str) -> dict[str, Any]:
    organization_id = _organization_id(organization_id)
    name = str(name or "").strip()
    if not name:
        raise ValueError("organization name is required")
    with FileLock(_path().with_name("users.lock")):
        data = _read()
        if any(item.get("organization_id") == organization_id for item in data["organizations"]):
            raise ValueError("organization already exists")
        record = {"organization_id": organization_id, "name": name[:120], "workspace_ids": []}
        data["organizations"].append(record)
        atomic_write_json(_path(), data)
    return record


def list_organizations() -> list[dict[str, Any]]:
    return [dict(item) for item in _read().get("organizations", [])]


def list_memberships(organization_id: str) -> list[dict[str, Any]]:
    organization_id = _organization_id(organization_id)
    return [dict(item) for item in _read().get("memberships", []) if item.get("organization_id") == organization_id]


def upsert_membership(organization_id: str, username: str, role: str, workspace_ids: list[str] | None = None) -> dict[str, Any]:
    organization_id = _organization_id(organization_id)
    username = str(username or "").strip()
    if not username or role not in _ROLES:
        raise ValueError("username and a valid role are required")
    from storage.ids import validate_workspace_id
    allowed = sorted({validate_workspace_id(item) for item in (workspace_ids or [])})
    with FileLock(_path().with_name("users.lock")):
        data = _read()
        organization = next((item for item in data["organizations"] if item.get("organization_id") == organization_id), None)
        if organization is None:
            raise ValueError("organization not found")
        if not any(item.get("username") == username for item in data["users"]):
            raise ValueError("user not found")
        if not set(allowed).issubset(set(organization.get("workspace_ids") or [])):
            raise ValueError("workspace is not assigned to the organization")
        memberships = [item for item in data["memberships"] if not (item.get("username") == username and item.get("organization_id") == organization_id)]
        record = {"username": username, "organization_id": organization_id, "role": role, "workspace_ids": allowed}
        memberships.append(record)
        data["memberships"] = memberships
        for user in data["users"]:
            if user.get("username") == username:
                user["organization_id"] = organization_id
                user["role"] = role
                user["workspace_ids"] = allowed
        atomic_write_json(_path(), data)
    return record


def assign_workspace(organization_id: str, workspace_id: str) -> dict[str, Any]:
    organization_id = _organization_id(organization_id)
    from storage.ids import validate_workspace_id
    workspace_id = validate_workspace_id(workspace_id)
    with FileLock(_path().with_name("users.lock")):
        data = _read()
        organization = next((item for item in data["organizations"] if item.get("organization_id") == organization_id), None)
        if organization is None:
            raise ValueError("organization not found")
        if any(item is not organization and workspace_id in set(item.get("workspace_ids") or []) for item in data["organizations"]):
            raise ValueError("workspace is already assigned to another organization")
        organization["workspace_ids"] = sorted(set(organization.get("workspace_ids") or []) | {workspace_id})
        atomic_write_json(_path(), data)
        return dict(organization)


def replace_workspace(old_workspace_id: str, new_workspace_id: str | None = None) -> None:
    from storage.ids import validate_workspace_id
    old_workspace_id = validate_workspace_id(old_workspace_id)
    replacement = validate_workspace_id(new_workspace_id) if new_workspace_id else None
    with FileLock(_path().with_name("users.lock")):
        data = _read()
        for collection in (data["organizations"], data["memberships"], data["users"]):
            for item in collection:
                workspaces = set(item.get("workspace_ids") or [])
                if old_workspace_id in workspaces:
                    workspaces.remove(old_workspace_id)
                    if replacement: workspaces.add(replacement)
                    item["workspace_ids"] = sorted(workspaces)
        atomic_write_json(_path(), data)
