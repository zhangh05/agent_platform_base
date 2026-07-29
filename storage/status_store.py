"""Workspace status aggregation."""

from __future__ import annotations

import json

from storage.paths import workspace_root
from storage.run_record_store import is_run_record_file


def workspace_exists(workspace_id: str) -> bool:
    return workspace_root(workspace_id).is_dir()


def index_health(workspace_id: str) -> str:
    ws = workspace_root(workspace_id)
    for idx_name in ("files.jsonl", "references.jsonl", "artifacts.jsonl"):
        idx = ws / "index" / idx_name
        if idx.exists() and idx.is_file():
            return "ok"
    return "missing"


def workspace_counts(workspace_id: str) -> dict[str, int]:
    """Return counts from current storage records, not directory entries."""
    ws = workspace_root(workspace_id)
    runs_dir = ws / "runs"
    run_count = sum(
        1 for path in runs_dir.glob("*.json")
        if path.is_file() and is_run_record_file(path)
    ) if runs_dir.is_dir() else 0

    artifact_count = 0
    artifact_index = ws / "index" / "artifacts.jsonl"
    if artifact_index.is_file():
        for line in artifact_index.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("lifecycle", "active") != "deleted":
                artifact_count += 1

    jobs_dir = ws / "jobs"
    job_count = sum(
        1 for path in jobs_dir.glob("*/*.json")
        if path.is_file() and path.name == f"{path.parent.name}.json"
    ) if jobs_dir.is_dir() else 0
    return {"runs": run_count, "artifacts": artifact_count, "jobs": job_count}
