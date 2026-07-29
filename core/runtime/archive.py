# runtime/archive.py
"""Runtime Archive — safe, dry-run-first archival of expired run/trace/job/temp data.

Moves candidates from active directories to workspace/archives/YYYY-MM/.
Default is dry-run only. Real archive requires confirm=True.
Never archives active refs, workspace state references, or workspace-external files.
"""

import json
import re
import shutil
import time
from dataclasses import dataclass, field

from core.runtime.lifecycle_base import (
    workspace_dir, is_safe_path, get_active_refs, scan_directory, write_audit,
)


@dataclass
class ArchivePolicy:
    runs_older_than_days: int = 30
    runs_keep_latest: int = 500
    traces_older_than_days: int = 30
    traces_keep_latest: int = 1000
    jobs_older_than_days: int = 30
    jobs_statuses: tuple = ("succeeded", "failed", "cancelled")
    temp_older_than_days: int = 7

    def as_dict(self) -> dict:
        return {
            "runs_older_than_days": self.runs_older_than_days,
            "runs_keep_latest": self.runs_keep_latest,
            "traces_older_than_days": self.traces_older_than_days,
            "traces_keep_latest": self.traces_keep_latest,
            "jobs_older_than_days": self.jobs_older_than_days,
            "temp_older_than_days": self.temp_older_than_days,
        }


@dataclass
class ArchivePreview:
    dry_run: bool = True
    workspace_id: str = ""
    policy: dict = field(default_factory=dict)
    candidate_counts: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    blocked_items: list = field(default_factory=list)
    moved_counts: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "workspace_id": self.workspace_id,
            "policy": self.policy,
            "candidate_counts": self.candidate_counts,
            "candidates": self.candidates[:50],
            "blocked_items": self.blocked_items[:20],
            "moved_counts": self.moved_counts,
            "warnings": self.warnings,
        }


def default_archive_policy() -> ArchivePolicy:
    return ArchivePolicy()


def preview_archive_candidates(workspace_id: str = "default",
                               policy: ArchivePolicy = None) -> ArchivePreview:
    """Preview archive candidates. NEVER moves files."""
    policy = policy or default_archive_policy()
    preview = ArchivePreview(
        dry_run=True,
        workspace_id=workspace_id,
        policy=policy.as_dict(),
    )
    try:
        ws_dir = workspace_dir(workspace_id)
    except ValueError:
        preview.warnings.append("Invalid workspace id")
        return preview

    if not ws_dir.exists():
        preview.warnings.append(f"Workspace '{workspace_id}' not found")
        return preview

    active_refs = get_active_refs(ws_dir)
    candidates = []
    blocked = []
    from storage.run_record_store import is_run_record_file

    # ═══ Runs (shared utility) ═══
    runs_result = scan_directory(ws_dir, "runs",
                                 max_age_days=policy.runs_older_than_days,
                                 max_count=policy.runs_keep_latest,
                                 active_refs=active_refs,
                                 name_filter=is_run_record_file)
    for name in runs_result["candidates"]:
        candidates.append({"type": "run", "name": name})
    blocked.extend(runs_result["blocked"])

    # ═══ Traces (shared utility) ═══
    traces_result = scan_directory(ws_dir, "runs",
                                   max_age_days=policy.traces_older_than_days,
                                   max_count=policy.traces_keep_latest,
                                   active_refs=active_refs,
                                   name_filter=lambda path: path.name.endswith(".trace.json"))
    for name in traces_result["candidates"]:
        candidates.append({"type": "trace", "name": name})
    blocked.extend(traces_result["blocked"])

    # ═══ Jobs (custom: terminal statuses only) ═══
    jobs_dir = ws_dir / "jobs"
    if jobs_dir.is_dir():
        now = time.time()
        for job_dir in jobs_dir.iterdir():
            if not job_dir.is_dir() or job_dir.name.startswith("."):
                continue
            jf = job_dir / f"{job_dir.name}.json"
            if not jf.is_file() or not is_safe_path(job_dir, ws_dir):
                blocked.append({"path": job_dir.name, "reason": "invalid_job_record"})
                continue
            if job_dir.name in active_refs:
                blocked.append({"path": job_dir.name, "reason": "active_ref"})
                continue
            try:
                record = json.loads(jf.read_text()) if jf.suffix == ".json" else {}
                job_status = record.get("status", "")
            except Exception:
                job_status = "unknown"
            if job_status not in policy.jobs_statuses:
                blocked.append({"path": jf.name, "reason": f"job_status:{job_status}"})
                continue
            age_days = (now - jf.stat().st_mtime) / 86400
            if age_days > policy.jobs_older_than_days:
                candidates.append({"type": "job", "name": job_dir.name})

    # ═══ Temp (shared utility) ═══
    temp_result = scan_directory(ws_dir, "files/tmp",
                                 max_age_days=policy.temp_older_than_days,
                                 active_refs=active_refs,
                                 check_is_file=False)
    for name in temp_result["candidates"]:
        candidates.append({"type": "temp", "name": name})
    blocked.extend(temp_result["blocked"])

    counts = {}
    for c in candidates:
        counts[c["type"]] = counts.get(c["type"], 0) + 1
    preview.candidate_counts = counts
    preview.candidates = candidates
    preview.blocked_items = blocked
    preview.moved_counts = {}
    return preview


def apply_archive(workspace_id: str = "default",
                  policy: ArchivePolicy = None,
                  dry_run: bool = True,
                  confirm: bool = False) -> ArchivePreview:
    """Apply archive. Requires confirm=True for actual file movement."""
    preview = preview_archive_candidates(workspace_id, policy)

    if dry_run:
        preview.dry_run = True
        preview.warnings.append("DRY RUN — no files were moved. Use dry_run=False + confirm=True to apply.")
        try:
            write_audit(
                audit_dir=workspace_dir(workspace_id) / "sys" / "audits",
                record_type="archive",
                workspace_id=workspace_id,
                dry_run=True,
                policy=preview.policy,
                candidate_counts=preview.candidate_counts,
                result_counts={},
                warnings=preview.warnings,
                blocked_count=len(preview.blocked_items),
            )
        except Exception:
            pass
        return preview

    if not confirm:
        preview.warnings.append("BLOCKED: confirm=True is required when dry_run=False.")
        return preview

    # Actually archive
    ws_dir = workspace_dir(workspace_id)
    month_key = time.strftime("%Y-%m")
    archive_root = ws_dir / "sys" / "archives" / month_key
    moved = {}

    for candidate in preview.candidates:
        ctype = candidate["type"]
        name = candidate["name"]
        src = None
        dst = None
        if ctype == "run":
            src = ws_dir / "runs" / name
            dst = archive_root / "runs" / name
        elif ctype == "trace":
            src = ws_dir / "runs" / name
            dst = archive_root / "traces" / name
        elif ctype == "job":
            src = ws_dir / "jobs" / name
            dst = archive_root / "jobs" / name
        elif ctype == "temp":
            src = ws_dir / "files" / "tmp" / name
            dst = archive_root / "tmp" / name
        if src and src.exists() and is_safe_path(src, ws_dir):
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                moved[ctype] = moved.get(ctype, 0) + 1
            except Exception as e:
                preview.warnings.append(f"Failed to move {name}: {str(e)[:100]}")
        else:
            preview.warnings.append(f"Skipping {name}: source missing or unsafe")

    preview.moved_counts = moved
    preview.dry_run = False
    # Write audit record (shared utility)
    write_audit(
        audit_dir=ws_dir / "sys" / "audits",
        record_type="archive",
        workspace_id=workspace_id,
        dry_run=False,
        policy=preview.policy,
        candidate_counts=preview.candidate_counts,
        result_counts=moved,
        warnings=preview.warnings,
        blocked_count=len(preview.blocked_items),
    )
    return preview


def get_archive_audits(workspace_id: str = "default") -> list:
    """List archive audit records."""
    audit_dir = workspace_dir(workspace_id) / "sys" / "audits"
    if not audit_dir.is_dir():
        return []
    audits = []
    for af in sorted(audit_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if af.name.startswith("archive_") and af.suffix == ".json":
            try:
                audits.append(json.loads(af.read_text()))
            except Exception:
                pass
    return audits[:50]


def get_archive_audit(workspace_id: str, audit_id: str) -> dict:
    """Get a specific archive audit record."""
    audit_path = workspace_dir(workspace_id) / "sys" / "audits" / f"{audit_id}.json"
    if audit_path.is_file():
        try:
            return json.loads(audit_path.read_text())
        except Exception:
            pass
    return {}


def list_archived_items(workspace_id: str = "default") -> list[dict]:
    """List restorable archive entries without exposing physical paths."""
    ws_dir = workspace_dir(workspace_id)
    root = ws_dir / "sys" / "archives"
    if not root.is_dir():
        return []
    items: list[dict] = []
    for month_dir in sorted(root.iterdir(), reverse=True):
        if not month_dir.is_dir() or not re.fullmatch(r"\d{4}-\d{2}", month_dir.name):
            continue
        for kind in ("runs", "traces", "jobs", "tmp"):
            kind_dir = month_dir / kind
            if not kind_dir.is_dir():
                continue
            for entry in sorted(kind_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
                if not is_safe_path(entry, ws_dir):
                    continue
                size_bytes = entry.stat().st_size if entry.is_file() else sum(
                    path.stat().st_size for path in entry.rglob("*") if path.is_file()
                )
                items.append({
                    "month": month_dir.name,
                    "kind": kind,
                    "name": entry.name,
                    "size_bytes": size_bytes,
                    "archived_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(entry.stat().st_mtime)),
                })
    return items


def restore_archived_item(
    workspace_id: str,
    *,
    month: str,
    kind: str,
    name: str,
    confirm: bool = False,
) -> dict:
    """Restore one archived entry to its canonical active location."""
    if not confirm:
        return {"ok": False, "error": "confirm_required"}
    if not re.fullmatch(r"\d{4}-\d{2}", str(month or "")):
        return {"ok": False, "error": "invalid_month"}
    destinations = {
        "runs": ("runs",),
        "traces": ("runs",),
        "jobs": ("jobs",),
        "tmp": ("files", "tmp"),
    }
    if kind not in destinations:
        return {"ok": False, "error": "invalid_kind"}
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name:
        return {"ok": False, "error": "invalid_name"}
    ws_dir = workspace_dir(workspace_id)
    source = ws_dir / "sys" / "archives" / month / kind / name
    target = ws_dir.joinpath(*destinations[kind], name)
    if not source.exists() or not is_safe_path(source, ws_dir):
        return {"ok": False, "error": "archive_item_not_found"}
    if target.exists():
        return {"ok": False, "error": "active_item_already_exists"}
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    write_audit(
        audit_dir=ws_dir / "sys" / "audits",
        record_type="archive_restore",
        workspace_id=workspace_id,
        dry_run=False,
        policy={"month": month, "kind": kind, "name": name},
        candidate_counts={kind: 1},
        result_counts={kind: 1},
        warnings=[],
    )
    return {"ok": True, "item": {"month": month, "kind": kind, "name": name}}
