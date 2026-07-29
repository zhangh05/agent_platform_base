#!/usr/bin/env python3
"""GC dry-run CLI — detect orphans, missing files, deleted artifacts without deleting anything."""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def detect_orphan_files(workspace_id: str) -> list[dict]:
    """Detect orphan FileRecords (indexed but physical file missing)."""
    from storage.file_store import list_files
    from storage.paths import workspace_root

    orphans = []
    ws = workspace_root(workspace_id)
    for rec in list_files(workspace_id, lifecycle=""):
        path = ws / rec.get("path", "")
        if not path.exists():
            orphans.append({"file_id": rec.get("file_id"), "path": rec.get("path"),
                            "logical_type": rec.get("logical_type")})
    return orphans


def detect_missing_files(workspace_id: str) -> list[dict]:
    """Detect files on disk with no FileRecord index entry."""
    from storage.file_store import list_files
    from storage.paths import workspace_root

    ws = workspace_root(workspace_id)
    indexed_paths = {r.get("path", "") for r in list_files(workspace_id, lifecycle="")}

    missing = []
    managed_dirs = ["files/data"]
    for md in managed_dirs:
        d = ws / md
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(ws))
                if rel not in indexed_paths:
                    missing.append({"path": rel, "abs_path": str(f), "size": f.stat().st_size})
    return missing


def detect_deleted_artifacts(workspace_id: str) -> list[dict]:
    """Detect artifacts with deleted lifecycle."""
    from artifacts.store import list_artifacts
    deleted = list_artifacts(workspace_id, include_deleted=True, limit=1000)
    return [a for a in deleted if a.get("lifecycle") == "deleted"]


def run_gc_dry_run(workspace_id: str) -> dict:
    """Run a read-only GC preview on one workspace."""
    return {
        "workspace_id": workspace_id,
        "dry_run": True,
        "orphan_files": detect_orphan_files(workspace_id),
        "missing_files": detect_missing_files(workspace_id),
        "deleted_artifacts": detect_deleted_artifacts(workspace_id),
        "candidates": [],
        "errors": [],
    }


def run_gc_all() -> list[dict]:
    """Run GC dry-run on all workspaces."""
    import re
    from storage.paths import get_workspace_root
    root = get_workspace_root()
    results = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", d.name):
                results.append(run_gc_dry_run(d.name))
    return results


def main():
    parser = argparse.ArgumentParser(description="GC dry-run preview")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--workspace", type=str, help="Inspect one workspace")
    group.add_argument("--all", action="store_true", help="Inspect all workspaces")
    args = parser.parse_args()

    if args.all:
        results = run_gc_all()
    else:
        results = [run_gc_dry_run(args.workspace)]

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
