# runtime/lifecycle_base.py
"""Shared utilities for Retention and Archive — eliminates code duplication.

Both retention.py and archive.py share:
  - Path safety check (_is_safe_path)
  - Active artifact/reference detection (_get_active_refs)
  - Directory scanning with age/count filtering
  - Audit record writing

This module provides the common building blocks.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from agent.runtime.utils import now_iso
from storage.atomic_io import atomic_write_json
from storage.paths import workspace_root


def workspace_dir(workspace_id: str) -> Path:
    return workspace_root(workspace_id)


def is_safe_path(path: Path, ws_dir: Path) -> bool:
    """Verify path is within workspace — uses relative_to(), NOT string startswith.

    Prevents path traversal, symlink escape, and cross-workspace access.
    """
    try:
        resolved = path.resolve()
        ws_resolved = ws_dir.resolve()
        resolved.relative_to(ws_resolved)
        return True
    except (ValueError, OSError):
        return False


def get_active_refs(ws_dir: Path) -> set:
    """Get artifact/job IDs referenced by workspace state and recent runs.

    These IDs are protected — never pruned or archived.
    Combines refs from:
      - workspace state.json (current_run_id, current_artifacts, etc.)
      - recent run records (artifact_refs)
    """
    active = set()

    # From workspace state
    state_file = ws_dir / "sys" / "state.json"
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text())
            for key in ("current_run_id", "last_run_id", "current_job_id", "last_job_id", "current_artifacts",
                        "last_input_artifacts", "last_output_artifacts",
                        "last_report_artifacts", "current_report_artifact_id",
                        "current_topology_artifact_id"):
                val = state.get(key)
                if isinstance(val, str):
                    active.add(val)
                elif isinstance(val, list):
                    for v in val:
                        if isinstance(v, str):
                            active.add(v)
                        elif isinstance(v, dict):
                            active.add(v.get("artifact_id", ""))
        except Exception:
            pass

    # Every session-owned run remains active until the session is removed by
    # the session repository.  Lifecycle jobs must never strand a session by
    # pruning or archiving one of its run records behind its back.
    sessions_dir = ws_dir / "sessions"
    if sessions_dir.is_dir():
        for session_file in sessions_dir.glob("*.json"):
            try:
                session = json.loads(session_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for run_id in session.get("run_ids") or []:
                if isinstance(run_id, str) and run_id:
                    active.add(run_id)

    # From all current run records. Limiting this scan makes protection depend
    # on directory iteration order and can expose older referenced artifacts.
    runs_dir = ws_dir / "runs"
    if runs_dir.is_dir():
        from storage.run_record_store import is_run_record_file

        for rf in runs_dir.iterdir():
            if not is_run_record_file(rf):
                continue
            try:
                record = json.loads(rf.read_text(encoding="utf-8-sig"))
                art_refs = record.get("artifact_refs", [])
                if isinstance(art_refs, list):
                    for ref in art_refs:
                        aid = ref.get("artifact_id", ref) if isinstance(ref, dict) else str(ref)
                        if aid:
                            active.add(aid)
            except Exception:
                pass

    return active


def scan_directory(ws_dir: Path, subdir: str, max_age_days: int = 0,
                   max_count: int = 0, active_refs: Optional[set] = None,
                   check_is_file: bool = True,
                   name_filter: Callable[[Path], bool] | None = None) -> dict:
    """Scan a workspace subdirectory for candidates based on age and count.

    Args:
        ws_dir: Workspace root directory.
        subdir: Subdirectory name (e.g., "runs", "traces", "jobs").
        max_age_days: Maximum age in days before a file is a candidate. 0 = no age filter.
        max_count: Keep at most this many newest files; older ones become candidates. 0 = no count filter.
        active_refs: Set of IDs that should be protected from pruning.
        check_is_file: If True, skip non-file entries.

    Returns:
        Dict with keys: 'candidates' (list of names), 'blocked' (list of dicts).
    """
    target_dir = ws_dir / subdir
    if not target_dir.is_dir():
        return {"candidates": [], "blocked": []}

    now = time.time()
    candidates = []
    blocked = []
    active_refs = active_refs or set()

    # Sort by mtime (oldest first) for consistent count-based pruning
    entries = sorted(
        (entry for entry in target_dir.iterdir() if name_filter is None or name_filter(entry)),
        key=lambda p: p.stat().st_mtime,
    )
    expired = []
    eligible = []

    for entry in entries:
        if check_is_file and not entry.is_file():
            continue
        if not is_safe_path(entry, ws_dir):
            blocked.append({"path": entry.name, "reason": "path_outside_workspace"})
            continue
        # Active ref protection
        reference_keys = {entry.name, entry.stem}
        if entry.name.endswith(".trace.json"):
            reference_keys.add(entry.name[:-len(".trace.json")])
        if reference_keys.intersection(active_refs):
            blocked.append({"path": entry.name, "reason": "active_ref"})
            continue
        eligible.append(entry)

        # Age-based filter
        if max_age_days > 0:
            age_days = (now - entry.stat().st_mtime) / 86400
            if age_days > max_age_days:
                expired.append(entry.name)

    # Count-based filter (keep newest max_count)
    if max_count > 0 and len(eligible) > max_count:
        for entry in eligible[:-max_count]:
            if entry.name not in expired:
                expired.append(entry.name)

    candidates = list(dict.fromkeys(expired))  # dedupe preserving order
    return {"candidates": candidates, "blocked": blocked}


def write_audit(audit_dir: Path, record_type: str, workspace_id: str,
                dry_run: bool, policy: dict, candidate_counts: dict,
                result_counts: dict, warnings: list, blocked_count: int = 0) -> str:
    """Write an audit record to the workspace's runtime_audits directory.

    Args:
        audit_dir: Path to runtime_audits directory.
        record_type: "retention" or "archive".
        workspace_id: Workspace identifier.
        dry_run: Whether this was a dry run.
        policy: The policy used.
        candidate_counts: Counts of candidates by type.
        result_counts: Counts of deleted/moved items by type.
        warnings: List of warning strings.
        blocked_count: Number of blocked items.

    Returns:
        The audit_id.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_id = f"{record_type}_{uuid.uuid4().hex[:12]}"
    record = {
        "audit_id": audit_id,
        "type": record_type,
        "created_at": now_iso(),
        "workspace_id": workspace_id,
        "dry_run": dry_run,
        "confirmed": not dry_run,
        "policy": policy,
        "candidate_counts": candidate_counts,
        "result_counts": result_counts,
        "blocked_count": blocked_count,
        "warnings": warnings[:20],
    }
    atomic_write_json(audit_dir / f"{audit_id}.json", record)
    return audit_id
