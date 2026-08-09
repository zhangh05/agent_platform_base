# storage/paths.py
"""Unified workspace path resolution.

All storage code and module code MUST use these functions instead of defining
their own workspace root constants.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_USER_ID_PATTERN = re.compile(r"^usr_[0-9a-f]{32}$")


def get_workspace_root() -> Path:
    """Return the workspace root directory, respecting env vars."""
    env = os.environ.get("NA_WORKSPACE_ROOT") or os.environ.get("AGENT_PLATFORM_WORKSPACE_DIR")
    return Path(env if env else REPO_ROOT / "workspaces").resolve()


def runtime_root() -> Path:
    """Return the storage-owned runtime root.

    Runtime state is durable application state but not a user workspace. Keep it
    under the workspace root so there is one data plane root to clean, back up,
    or relocate.
    """
    return get_workspace_root() / "_runtime"


def user_data_root(user_id: str) -> Path:
    """Return the validated root that owns one user's durable data."""
    value = str(user_id or "").strip()
    if not _USER_ID_PATTERN.fullmatch(value):
        raise ValueError("invalid user storage id")
    return get_workspace_root() / "users" / value


def user_runtime_root() -> Path:
    """Return the current principal's runtime-data root.

    Runtime-wide service state remains in ``runtime_root``. User-visible
    runtime records belong inside the user's own root, never beside other
    users under the platform runtime directory.
    """
    from storage.principal import (
        current_storage_principal,
        principal_storage_key,
    )

    principal = current_storage_principal()
    if not principal:
        return runtime_root()
    return user_data_root(principal_storage_key(principal)) / "runtime"


def workspace_root(workspace_id: str) -> Path:
    """Return the current principal's data root for a logical workspace."""
    from storage.ids import validate_workspace_id
    from storage.principal import (
        current_storage_principal,
        principal_storage_key,
    )
    ws_id = validate_workspace_id(workspace_id)
    principal = current_storage_principal()
    if not principal:
        # Internal bootstrap and maintenance calls have no user identity. API
        # requests always bind one before business storage is accessed.
        return get_workspace_root() / ws_id
    return get_workspace_root() / "users" / principal_storage_key(principal) / "workspaces" / ws_id


def workspace_catalog_root(workspace_id: str) -> Path:
    """Return the shared control-plane root for a logical workspace."""
    from storage.ids import validate_workspace_id
    return get_workspace_root() / "catalog" / validate_workspace_id(workspace_id)


def ensure_workspace_storage_dirs(workspace_id: str) -> None:
    """Create all standard storage directories for a workspace."""
    ws = workspace_root(workspace_id)
    for rel in [
        "files/data",
        "files/tmp",
        # System dirs
        "index",
        "context",
        "sessions",
        "runs",
        "sys",
        "inbox",
    ]:
        (ws / rel).mkdir(parents=True, exist_ok=True)
