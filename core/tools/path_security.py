# core/tools/path_security.py
"""Safe workspace path resolver — blocks path traversal, symlink escape, and encoding bypass.

All workspace file paths MUST go through this resolver. It guarantees:
1. Path resolves within the workspace root
2. No ../ (or encoded variants) escape
3. No absolute path injection
4. No symlink escape to outside workspace
5. No Windows drive-path injection (C:\\, D:\\)
6. No URL-encoded traversal (%2e%2e/)
7. No prefix-spoofing containment bypass (v2.1.3: uses Path.relative_to)

Usage:
    from core.tools.path_security import safe_workspace_path
    target = safe_workspace_path("workspace_id", "subdir/file.txt")
    # Returns a resolved Path or raises PathSecurityError
"""

import os
import re
import urllib.parse
from pathlib import Path

from storage.workspace_files import resolve_workspace_path
from storage.ids import validate_workspace_id


class PathSecurityError(ValueError):
    """Raised when a path violates workspace security boundaries."""
    pass


# ── Patterns for traversal detection ──
_TRAVERSAL_PATTERNS = [
    # Literal ../
    re.compile(r"(^|[/\\])\.\."),
    # URL-encoded traversal: %2e%2e, %2E%2E, %2e%2E, etc.
    re.compile(r"(%[2Ff]%[2Ee]%[2Ee])", re.IGNORECASE),
    re.compile(r"(%[2Ee]{2}%[2Ff])", re.IGNORECASE),
    # Backslash traversal (Windows-style)
    re.compile(r"(^|[\\])\.\."),
    # Double URL encoding
    re.compile(r"%25[2Ff2Ee]"),
]

# ── Windows drive path pattern ──
_WIN_DRIVE = re.compile(r"^[A-Za-z]:[/\\]")


def _is_contained(path: Path, base: Path) -> bool:
    """Check if path is contained within base using Path.relative_to.

    v2.1.3: Replaces string startswith with Path.relative_to to prevent
    prefix-spoofing attacks (e.g., /workspaces/default_evil bypassing
    a check against /workspaces/default).
    """
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _contains_traversal(subpath: str) -> bool:
    """Check if subpath contains path traversal attempts."""
    # Decode URL encoding first
    decoded = subpath
    try:
        decoded = urllib.parse.unquote(subpath)
    except Exception:
        pass  # If unquote fails, it's likely not a valid encoded path

    # Check raw and decoded
    for candidate in (subpath, decoded):
        # Windows drive path
        if _WIN_DRIVE.match(candidate):
            return True
        # Absolute path
        if candidate.startswith("/") or candidate.startswith("\\"):
            return True
        # Traversal patterns
        for pat in _TRAVERSAL_PATTERNS:
            if pat.search(candidate):
                return True

    return False


def safe_workspace_path(workspace_id: str, subpath: str = "") -> Path:
    """Validate and resolve a workspace file path securely.

    Blocks:
    - Path traversal (../, ..\\, encoded variants)
    - Absolute paths (/etc/passwd)
    - Windows drive paths (C:\\Windows)
    - Symlink escapes to outside workspace root
    - URL-encoded traversal (%2e%2e%2f)
    - Prefix-spoofing containment bypass (e.g., default_evil)

    Args:
        workspace_id: Validated workspace identifier.
        subpath: Workspace-relative file path.

    Returns:
        Resolved absolute Path within the workspace.

    Raises:
        PathSecurityError on any security violation.
    """
    # ── 1. Validate workspace_id ──
    try:
        ws_id = validate_workspace_id(workspace_id)
    except ValueError as e:
        raise PathSecurityError(f"invalid_workspace_id: {e}") from e

    base = resolve_workspace_path(ws_id, "").resolve()

    # ── 2. Check for traversal in raw subpath ──
    if _contains_traversal(subpath):
        raise PathSecurityError(f"path_escape_denied: traversal detected in '{subpath}'")

    # ── 3. Normalize: remove leading slashes, collapse redundant separators ──
    clean = subpath.lstrip("/").lstrip("\\")
    # Normalize path separators to OS-native
    clean = clean.replace("\\", "/")
    # Collapse multiple slashes
    clean = re.sub(r"/{2,}", "/", clean)

    # ── 4. Re-check after normalization ──
    if clean != subpath.lstrip("/").lstrip("\\") and _contains_traversal(clean):
        raise PathSecurityError(f"path_escape_denied: traversal after normalization in '{subpath}'")

    # ── 5. Resolve and check boundaries ──
    target = (base / clean).resolve()

    # v2.1.3: Use Path.relative_to for containment (not string startswith)
    if not _is_contained(target, base):
        raise PathSecurityError(
            f"path_escape_denied: '{subpath}' resolves outside workspace '{base}'"
        )

    if not clean:
        base.mkdir(parents=True, exist_ok=True)
        return base

    # ── 6. Symlink check: verify real path is also contained ──
    try:
        if target.exists() or target.is_symlink():
            real_target = target.resolve()
            # v2.1.3: Use Path.relative_to for symlink containment
            if not _is_contained(real_target, base):
                raise PathSecurityError(
                    f"path_escape_denied: symlink '{subpath}' points outside workspace"
                )
        # For non-existent paths: verify parent's real path
        else:
            parent = target.parent
            while parent != base and not parent.exists():
                if parent == parent.parent:
                    raise PathSecurityError(
                        f"path_escape_denied: parent for '{subpath}' is outside workspace"
                    )
                parent = parent.parent
            if not _is_contained(parent, base):
                raise PathSecurityError(
                    f"path_escape_denied: parent for '{subpath}' is outside workspace"
                )
            if parent.exists() or parent.is_symlink():
                real_parent = parent.resolve()
                # v2.1.3: Use Path.relative_to for parent symlink containment
                if not _is_contained(real_parent, base):
                    raise PathSecurityError(
                        f"path_escape_denied: parent symlink '{subpath}' points outside workspace"
                    )
    except (OSError, RuntimeError) as e:
        raise PathSecurityError(f"invalid_workspace_path: {e}") from e

    return target
