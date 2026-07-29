# runtime/retention.py
"""Runtime retention and prune — safe, dry-run-first data lifecycle management.

Principles:
  - Default is dry-run only — never delete without explicit flag.
  - Active artifacts and referenced outputs are never pruned.
  - Workspace boundaries are absolute — no path traversal.
  - All operations are auditable with structured output.
"""

import json
import time
from dataclasses import dataclass, field

from core.runtime.lifecycle_base import (
    workspace_dir, is_safe_path, get_active_refs, scan_directory, write_audit,
)


# Default policy (all in days or counts)
DEFAULT_POLICY = {
    "runs": {"max_age_days": 30, "max_count": 500},
    "traces": {"max_age_days": 30, "max_count": 1000},
    "jobs": {"max_age_days": 30},
    "artifacts": {"temp_max_age_days": 7},
    "reports": {"auto_prune": False},  # reports are never auto-pruned
    "sessions": {"max_age_days": 90, "deleted_max_age_days": 7},  # v3.1.1
}


@dataclass
class RetentionPolicy:
    """Retention policy for a workspace."""
    runs_max_age_days: int = 30
    runs_max_count: int = 500
    traces_max_age_days: int = 30
    traces_max_count: int = 1000
    jobs_max_age_days: int = 30
    artifacts_temp_max_age_days: int = 7
    prune_reports: bool = False
    sessions_max_age_days: int = 90          # v3.1.1
    sessions_deleted_max_age_days: int = 7   # v3.1.1: soft-deleted sessions

    def as_dict(self) -> dict:
        return {
            "runs_max_age_days": self.runs_max_age_days,
            "runs_max_count": self.runs_max_count,
            "traces_max_age_days": self.traces_max_age_days,
            "traces_max_count": self.traces_max_count,
            "jobs_max_age_days": self.jobs_max_age_days,
            "artifacts_temp_max_age_days": self.artifacts_temp_max_age_days,
            "prune_reports": self.prune_reports,
        }


@dataclass
class RetentionPreview:
    """Result of a retention preview (dry-run)."""
    dry_run: bool = True
    workspace_id: str = ""
    policy: dict = field(default_factory=dict)
    candidate_counts: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    blocked_items: list = field(default_factory=list)
    deleted_counts: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "workspace_id": self.workspace_id,
            "policy": self.policy,
            "candidate_counts": self.candidate_counts,
            "candidates": self.candidates[:50],
            "blocked_items": self.blocked_items[:20],
            "deleted_counts": self.deleted_counts,
            "warnings": self.warnings,
        }


def default_retention_policy() -> RetentionPolicy:
    return RetentionPolicy()


def preview_retention(workspace_id: str = "default",
                      policy: RetentionPolicy = None) -> RetentionPreview:
    """Preview what would be pruned. NEVER deletes. Always safe."""
    policy = policy or default_retention_policy()
    ws_dir = workspace_dir(workspace_id)
    preview = RetentionPreview(
        dry_run=True,
        workspace_id=workspace_id,
        policy=policy.as_dict(),
    )

    if not ws_dir.exists():
        preview.warnings.append(f"Workspace '{workspace_id}' not found")
        return preview

    active_artifacts = get_active_refs(ws_dir)
    candidates = []
    blocked = []
    now = time.time()
    from storage.run_record_store import is_run_record_file

    # ── Scan runs (shared utility) ──
    runs_result = scan_directory(ws_dir, "runs",
                                 max_age_days=policy.runs_max_age_days,
                                 max_count=policy.runs_max_count,
                                 active_refs=active_artifacts,
                                 name_filter=is_run_record_file)
    for name in runs_result["candidates"]:
        candidates.append({"type": "run", "name": name})
    blocked.extend(runs_result["blocked"])
    preview.candidate_counts["runs"] = len([c for c in candidates if c["type"] == "run"])

    # ── Scan traces (shared utility) ──
    traces_result = scan_directory(ws_dir, "runs",
                                   max_age_days=policy.traces_max_age_days,
                                   max_count=policy.traces_max_count,
                                   active_refs=active_artifacts,
                                   name_filter=lambda path: path.name.endswith(".trace.json"))
    for name in traces_result["candidates"]:
        candidates.append({"type": "trace", "name": name})
    blocked.extend(traces_result["blocked"])
    preview.candidate_counts["traces"] = len([c for c in candidates if c["type"] == "trace"])

    # ── Scan jobs (shared utility) ──
    jobs_dir = ws_dir / "jobs"
    if jobs_dir.is_dir():
        for job_dir in jobs_dir.iterdir():
            if not job_dir.is_dir() or job_dir.name.startswith("."):
                continue
            record_path = job_dir / f"{job_dir.name}.json"
            if not record_path.is_file() or not is_safe_path(job_dir, ws_dir):
                blocked.append({"path": job_dir.name, "reason": "invalid_job_record"})
                continue
            if job_dir.name in active_artifacts:
                blocked.append({"path": job_dir.name, "reason": "active_ref"})
                continue
            age_days = (now - record_path.stat().st_mtime) / 86400
            if age_days > policy.jobs_max_age_days:
                candidates.append({"type": "job", "name": job_dir.name})
    preview.candidate_counts["jobs"] = len([c for c in candidates if c["type"] == "job"])

    # ── Scan transient managed files only; durable files/data is never pruned here. ──
    temp_result = scan_directory(
        ws_dir,
        "files/tmp",
        max_age_days=policy.artifacts_temp_max_age_days,
        active_refs=active_artifacts,
        check_is_file=False,
    )
    for name in temp_result["candidates"]:
        candidates.append({"type": "artifact", "name": name})
    blocked.extend(temp_result["blocked"])
    preview.candidate_counts["artifacts"] = len([c for c in candidates if c["type"] == "artifact"])

    # ── Scan expired memories ──
    try:
        from storage.memory_governance import MemoryStore
        store = MemoryStore()
        mem_preview = store.cleanup_expired(dry_run=True)
        expired_count = mem_preview.get("removed_count", 0)
        if expired_count > 0:
            candidates.append({"type": "memory_expired", "name": f"{expired_count}_expired_memories", "count": expired_count})
    except Exception:
        pass  # Memory store not available — skip
    preview.candidate_counts["memories"] = len([c for c in candidates if c["type"] == "memory_expired"])

    # ── Scan sessions (v3.1.1) ──
    sessions_dir = ws_dir / "sessions"
    if sessions_dir.is_dir():
        for sf in sessions_dir.iterdir():
            if sf.suffix != ".json":
                continue
            if not is_safe_path(sf, ws_dir):
                blocked.append({"path": sf.name, "reason": "path_not_in_workspace"})
                continue
            try:
                session_data = json.loads(sf.read_text())
                status = session_data.get("status", "active")
                updated_at = session_data.get("updated_at", "")
            except Exception:
                status = "unknown"
                updated_at = ""
            # Parse updated_at and calculate age
            age_days = float("inf")
            if updated_at:
                try:
                    from datetime import datetime as _dt
                    ts = _dt.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
                    age_days = (now - ts) / 86400
                except Exception:
                    pass
            # Soft-deleted sessions: clean up faster
            if status == "deleted" and age_days > policy.sessions_deleted_max_age_days:
                candidates.append({"type": "session_deleted", "name": sf.name, "sid": sf.stem})
            elif status in ("active", "archived") and age_days > policy.sessions_max_age_days:
                if sf.stem not in active_artifacts:
                    candidates.append({"type": "session_expired", "name": sf.name, "sid": sf.stem})
    preview.candidate_counts["sessions"] = len([c for c in candidates if c["type"].startswith("session")])

    preview.candidates = candidates
    preview.blocked_items = blocked
    preview.deleted_counts = {}  # dry-run: nothing deleted
    return preview


def apply_retention(workspace_id: str = "default",
                    policy: RetentionPolicy = None,
                    dry_run: bool = True,
                    confirm: bool = False) -> RetentionPreview:
    """Apply retention. Requires confirm=True for actual deletion.

    Args:
        dry_run: If True (default), preview only, nothing deleted.
        confirm: Must be True when dry_run=False. Safety guard.
    """
    preview = preview_retention(workspace_id, policy)
    preview.dry_run = dry_run

    if dry_run:
        preview.warnings.append("DRY RUN — no files were deleted. Use dry_run=False + confirm=True to apply.")
        return preview

    if not confirm:
        preview.warnings.append("BLOCKED: confirm=True is required when dry_run=False.")
        return preview

    # Actually delete
    ws_dir = workspace_dir(workspace_id)
    deleted = {"runs": 0, "traces": 0, "jobs": 0, "artifacts": 0, "memories": 0, "sessions": 0}

    for candidate in preview.candidates:
        path = None
        ctype = candidate["type"]
        name = candidate["name"]
        if ctype == "memory_expired":
            # Handle memory expiration via store API
            try:
                from storage.memory_governance import MemoryStore
                store = MemoryStore()
                mem_result = store.cleanup_expired(dry_run=False)
                deleted["memories"] = mem_result.get("removed_count", 0)
            except Exception as e:
                preview.warnings.append(f"Failed to cleanup expired memories: {str(e)[:100]}")
            continue
        try:
            if ctype == "run":
                path = ws_dir / "runs" / name
            elif ctype == "trace":
                path = ws_dir / "runs" / name
            elif ctype == "job":
                path = ws_dir / "jobs" / name
            elif ctype == "artifact":
                path = ws_dir / "files" / "tmp" / name
            elif ctype in ("session_expired", "session_deleted"):
                # P1 fix (round 7): validate `sid` is a plain identifier
                # before using it as a directory name. Previous code used
                # `name.replace(".json", "")` which produced wrong paths
                # when `name` was e.g. a renamed file (`abc.json.bak`) or
                # a path-like value, leading to shutil.rmtree on the
                # wrong directory or raising on a non-existent path.
                import re as _re
                sid_raw = candidate.get("sid") or (name[:-5] if name.endswith(".json") else name)
                if (
                    not isinstance(sid_raw, str)
                    or sid_raw in (".", "..")
                    or not _re.fullmatch(r"[A-Za-z0-9_\-\.]{1,128}", sid_raw)
                ):
                    preview.warnings.append(
                        f"refused to delete session dir for malformed sid={sid_raw!r}"
                    )
                    continue
                from storage.session_store import delete_session_permanently
                if delete_session_permanently(sid_raw, workspace_id, confirm=True):
                    deleted["sessions"] += 1
                else:
                    preview.warnings.append(f"Failed to remove session={sid_raw}")
                continue
            if path and path.exists() and is_safe_path(path, ws_dir):
                if path.is_dir():
                    import shutil
                    shutil.rmtree(path)
                else:
                    path.unlink()
                key = "sessions" if ctype.startswith("session") else ctype
                deleted[key] = deleted.get(key, 0) + 1
        except Exception as e:
            preview.warnings.append(f"Failed to delete {name}: {str(e)[:100]}")

    preview.deleted_counts = deleted
    # Write audit record (shared utility)
    write_audit(
        audit_dir=ws_dir / "sys" / "audits",
        record_type="retention",
        workspace_id=workspace_id,
        dry_run=False,
        policy=preview.policy,
        candidate_counts=preview.candidate_counts,
        result_counts=deleted,
        warnings=preview.warnings,
        blocked_count=len(preview.blocked_items),
    )
    return preview


def get_audits(workspace_id: str = "default") -> list:
    """List retention audit records."""
    audit_dir = workspace_dir(workspace_id) / "sys" / "audits"
    if not audit_dir.is_dir():
        return []
    audits = []
    for af in sorted(audit_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if af.suffix == ".json" and not af.name.startswith("archive_"):
            try:
                audits.append(json.loads(af.read_text()))
            except Exception:
                pass
    return audits[:50]


def get_audit(workspace_id: str, audit_id: str) -> dict:
    """Get a specific retention audit record."""
    audit_path = workspace_dir(workspace_id) / "sys" / "audits" / f"{audit_id}.json"
    if audit_path.is_file():
        try:
            return json.loads(audit_path.read_text())
        except Exception:
            pass
    return {}
