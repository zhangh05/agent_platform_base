# storage/paths.py
"""Unified workspace path resolution.

All storage code and module code MUST use these functions instead of defining
their own workspace root constants.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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


def user_runtime_root() -> Path:
    """Return the current principal's runtime-data root.

    Runtime-wide service state remains in ``runtime_root``. User-visible
    runtime records (for example approval audits) belong below this root but
    must not be shared between authenticated users. The configured legacy
    administrator deliberately retains the pre-identity location.
    """
    from storage.principal import (
        current_storage_principal,
        principal_storage_key,
        uses_legacy_admin_storage,
    )

    principal = current_storage_principal()
    if not principal or uses_legacy_admin_storage(principal):
        return runtime_root()
    return runtime_root() / "users" / principal_storage_key(principal)


def workspace_root(workspace_id: str) -> Path:
    """Return user-scoped data root for a logical workspace."""
    from storage.ids import validate_workspace_id
    from storage.principal import (
        current_storage_principal,
        principal_storage_key,
        uses_legacy_admin_storage,
    )
    logical_root = get_workspace_root() / validate_workspace_id(workspace_id)
    principal = current_storage_principal()
    if not principal or uses_legacy_admin_storage(principal):
        return logical_root
    return logical_root / "users" / principal_storage_key(principal)


def workspace_catalog_root(workspace_id: str) -> Path:
    """Return the shared control-plane root for a logical workspace."""
    from storage.ids import validate_workspace_id
    return get_workspace_root() / validate_workspace_id(workspace_id)


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
