"""Workspace file repository helpers for tool handlers."""

from __future__ import annotations

from pathlib import Path

from storage.atomic_io import atomic_write_text
from storage.ids import validate_run_id
from storage.paths import workspace_root

_WRITE_DIRS = (
    "files/data",
    "files/tmp",
    "inbox",
)

_IMPORT_ROOTS = (
    "files/data",
    "files/tmp",
    "inbox",
)


def resolve_workspace_path(workspace_id: str, subpath: str = "") -> Path:
    root = workspace_root(workspace_id).resolve()
    target = (root / str(subpath or "").lstrip("/").lstrip("\\")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path_escape_denied") from exc
    return target


def is_current_workspace_write_path(workspace_id: str, target: Path) -> bool:
    root = resolve_workspace_path(workspace_id, "")
    try:
        rel = target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    return any(rel == allowed or rel.startswith(f"{allowed}/") for allowed in _WRITE_DIRS)


def write_text_atomic(path: Path, content: str) -> None:
    atomic_write_text(path, content)


def write_python_temp_script(workspace_id: str, run_id: str, content: str) -> tuple[Path, Path]:
    safe_run_id = validate_run_id(run_id)
    temp_dir = resolve_workspace_path(workspace_id, f"files/tmp/python_exec/{safe_run_id}")
    script_path = temp_dir / "script.py"
    atomic_write_text(script_path, content)
    return temp_dir, script_path


def resolve_importable_workspace_path(workspace_id: str, filepath: str) -> Path:
    target = resolve_workspace_path(workspace_id, filepath)
    root = resolve_workspace_path(workspace_id, "")
    rel = target.relative_to(root).as_posix()
    if not (rel == "inbox" or rel.startswith("inbox/")):
        raise ValueError("path_not_allowed")
    return target


def allowed_import_roots(workspace_id: str) -> list[Path]:
    root = resolve_workspace_path(workspace_id, "")
    return [root / rel for rel in _IMPORT_ROOTS]
