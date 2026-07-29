# storage/gc.py
"""Storage garbage collection — dry-run only in this version."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.paths import workspace_root
from storage.file_store import list_files


def find_orphan_files(workspace_id: str) -> list[dict]:
    """Find physical files in managed dirs that have no FileRecord."""
    ws = workspace_root(workspace_id)
    indexed_paths = {r["path"] for r in list_files(workspace_id, lifecycle="")}
    orphans = []
    for managed_dir in [
        "files/data",
        "files/tmp",
    ]:
        d = ws / managed_dir
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file():
                rel = f.relative_to(ws).as_posix()
                if rel not in indexed_paths:
                    orphans.append({"path": rel, "size": f.stat().st_size})
    return orphans


def find_soft_deleted_files(workspace_id: str) -> list[dict]:
    """Find files marked as soft_deleted."""
    return list_files(workspace_id, lifecycle="soft_deleted")


def find_missing_file_records(workspace_id: str) -> list[dict]:
    """Find FileRecords whose physical file is missing from disk."""
    ws = workspace_root(workspace_id)
    missing = []
    for rec in list_files(workspace_id, lifecycle=""):
        path = ws / rec["path"]
        if not path.exists():
            missing.append(rec)
    return missing


def gc_preview(workspace_id: str) -> dict[str, Any]:
    """Return a dry-run GC report (no deletions)."""
    return {
        "workspace_id": workspace_id,
        "orphan_files": find_orphan_files(workspace_id),
        "soft_deleted": find_soft_deleted_files(workspace_id),
        "missing_on_disk": find_missing_file_records(workspace_id),
    }
