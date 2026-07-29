# jobs/store.py
"""Job store — CRUD for jobs, events, logs in workspace directories."""

import json, shutil
import logging
from typing import Optional

from jobs.schemas import JobRecord, JobEvent
from jobs.redaction import (
    sanitize_job_record_for_storage, sanitize_job_record_for_api,
    sanitize_job_event_for_storage,
    sanitize_job_log_for_storage,
)
from storage.time_utils import now_iso
from storage.atomic_io import atomic_write_json
from storage.records import append_jsonl, read_jsonl
from storage.locking import FileLock
from storage.ids import validate_job_id, validate_workspace_id

_LOG = logging.getLogger("jobs.store")

def _get_ws_root():
    from storage.paths import get_workspace_root
    return get_workspace_root()

def _job_dir(ws_id, job_id=""):
    ws_id = validate_workspace_id(ws_id)
    safe_job_id = validate_job_id(job_id) if job_id else ""
    return _get_ws_root() / ws_id / "jobs" / safe_job_id

def _ensure(ws_id, job_id=""):
    d = _job_dir(ws_id, job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d

def _index_path(ws_id):
    ws_id = validate_workspace_id(ws_id)
    return _get_ws_root() / ws_id / "sys" / "jobs.index.json"


def _job_lock_path(ws_id, job_id):
    ws_id = validate_workspace_id(ws_id)
    job_id = validate_job_id(job_id)
    return _get_ws_root() / ws_id / "jobs" / ".locks" / f"{job_id}.lock"


def create_job(rec: JobRecord) -> JobRecord:
    from storage.workspace_store import ensure_workspace
    ensure_workspace(rec.workspace_id)
    d = _ensure(rec.workspace_id, rec.job_id)
    safe = sanitize_job_record_for_storage(rec.as_dict())
    with FileLock(_job_lock_path(rec.workspace_id, rec.job_id)):
        atomic_write_json(d / f"{rec.job_id}.json", safe)
    # Apply sanitization to rec before returning
    for k, v in safe.items():
        if hasattr(rec, k):
            setattr(rec, k, v)
    _update_index(rec.workspace_id, rec)
    append_event(rec.workspace_id, rec.job_id,
                 JobEvent(job_id=rec.job_id, workspace_id=rec.workspace_id,
                          event_type="job_created", message=f"Job created: {rec.title}"))
    _update_workspace_stats(rec.workspace_id)
    return rec


def get_job(ws_id, job_id) -> Optional[JobRecord]:
    if not job_id:
        return None
    path = _job_dir(ws_id, job_id) / f"{job_id}.json"
    if not path.is_file(): return None
    try:
        d = json.loads(path.read_text())
        return JobRecord(**{k: v for k, v in d.items() if k in JobRecord.__dataclass_fields__})
    except json.JSONDecodeError:
        import logging
        logging.getLogger("jobs.store").error("corrupt job file: %s", path)
        return None
    except Exception:
        import logging
        logging.getLogger("jobs.store").exception("unexpected error reading job: %s", path)
        return None


def update_job(ws_id, job_id, patch: dict) -> Optional[JobRecord]:
    with FileLock(_job_lock_path(ws_id, job_id)):
        rec = get_job(ws_id, job_id)
        if not rec: return None
        for k, v in patch.items():
            if hasattr(rec, k):
                setattr(rec, k, v)
        rec.updated_at = now_iso()
        d = _ensure(ws_id, job_id)
        safe = sanitize_job_record_for_storage(rec.as_dict())
        atomic_write_json(d / f"{job_id}.json", safe)
        for k, v in safe.items():
            if hasattr(rec, k):
                setattr(rec, k, v)
    _update_workspace_stats(ws_id)
    return rec


def _session_exists(ws_id, session_id):
    """Check if a session still exists and is not soft-deleted.
    
    Sessions can exist in two forms:
    1. {session_id}.json  — session metadata file
    2. {session_id}/      — session directory with messages/
    
    A session is considered alive if either form exists AND it's not marked deleted.
    """
    if not session_id:
        return False
    base = _get_ws_root() / ws_id / "sessions"
    meta_file = base / f"{session_id}.json"
    meta_dir  = base / str(session_id)
    
    # Must have at least one form of session storage
    if not meta_file.is_file() and not meta_dir.is_dir():
        return False
    
    # If metadata file exists, check it's not deleted
    if meta_file.is_file():
        try:
            d = json.loads(meta_file.read_text())
            if d.get("status", "active") == "deleted":
                return False
        except Exception:
            pass  # corrupt file → treat as exists
    
    return True


def list_jobs(ws_id=None, status=None, job_type=None, limit=100) -> list:
    results = []
    ws_root = _get_ws_root()
    if ws_id:
        ws_id = validate_workspace_id(ws_id)
    for wd in ws_root.iterdir() if not ws_id else [ws_root / ws_id]:
        if not wd.is_dir() or wd.name.startswith("."): continue
        jd = wd / "jobs"
        if not jd.is_dir(): continue
        for f in sorted(jd.glob("*/*.json"), reverse=True):
            if not f.name.endswith("_meta.json") and "events" not in str(f) and "log" not in str(f):
                j = get_job(wd.name, f.stem)
                if not j: continue
                if ws_id and j.workspace_id != ws_id: continue
                if status and j.status != status: continue
                if job_type and j.job_type != job_type: continue
                # Filter out agent_run jobs whose session no longer exists
                if j.job_type == "agent_run":
                    sid = (j.payload or {}).get("session_id", "")
                    if sid and not _session_exists(wd.name, sid):
                        continue
                results.append(sanitize_job_record_for_api(j.as_dict()))
                if len(results) >= limit: break
    return results


def delete_job(ws_id, job_id, soft=True) -> bool:
    if not job_id:
        raise ValueError("job_id is required for delete_job")
    if soft:
        return bool(update_job(ws_id, job_id, {"status": "cancelled", "cancel_requested": True}))
    with FileLock(_job_lock_path(ws_id, job_id)):
        shutil.rmtree(_job_dir(ws_id, job_id), ignore_errors=True)
    _remove_from_index(ws_id, job_id)
    _update_workspace_stats(ws_id)
    return True


def append_event(ws_id, job_id, event: JobEvent) -> JobEvent:
    _ensure(ws_id, job_id)
    append_jsonl(ws_id, ("jobs", job_id, f"{job_id}.events.jsonl"),
                 sanitize_job_event_for_storage(event.as_dict()))
    return event


def list_events(ws_id, job_id, limit=200) -> list:
    return read_jsonl(ws_id, ("jobs", job_id, f"{job_id}.events.jsonl"))[-limit:]


def append_log(ws_id, job_id, message, level="info", meta=None):
    _ensure(ws_id, job_id)
    entry = {"ts": now_iso(), "level": level,
             "msg": message[:1000], "meta": meta or {}}
    # Sanitize before writing
    safe = sanitize_job_log_for_storage(entry)
    append_jsonl(ws_id, ("jobs", job_id, f"{job_id}.log.jsonl"), safe)


def list_logs(ws_id, job_id, limit=200) -> list:
    return read_jsonl(ws_id, ("jobs", job_id, f"{job_id}.log.jsonl"))[-limit:]


def get_next_queued_job() -> Optional[JobRecord]:
    ws_root = _get_ws_root()
    for wd in sorted(ws_root.iterdir(), reverse=True):
        if not wd.is_dir(): continue
        jd = wd / "jobs"
        if not jd.is_dir(): continue
        for f in sorted(jd.glob("*/*.json")):
            j = get_job(wd.name, f.stem)
            if j and j.status == "queued": return j
    return None


def reconcile_running_jobs(finished_at: str, started_before: str) -> int:
    """Mark jobs left running by a previous backend process as failed."""
    from storage.workspace_store import list_workspace_ids

    reconciled = 0
    for ws_id in list_workspace_ids():
        jobs_dir = _get_ws_root() / ws_id / "jobs"
        if not jobs_dir.is_dir():
            continue
        for path in jobs_dir.glob("*/*.json"):
            if path.name != f"{path.parent.name}.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("status") != "running":
                continue
            if str(data.get("updated_at") or "") >= started_before:
                continue
            result = update_job(ws_id, str(data.get("job_id") or path.parent.name), {
                "status": "failed",
                "finished_at": finished_at,
                "error": "backend_restart_during_job",
            })
            if result:
                reconciled += 1
    return reconciled


# ── helpers ──

def _update_index(ws_id, rec):
    p = _index_path(ws_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p.with_name(p.name + ".lock")):
        idx = {"job_ids": [], "updated_at": ""}
        if p.is_file():
            try:
                idx = json.loads(p.read_text())
            except Exception:
                _LOG.warning("jobs.store: corrupt index", exc_info=True)
        if rec.job_id not in idx.setdefault("job_ids", []):
            idx["job_ids"].append(rec.job_id)
        idx["updated_at"] = now_iso()
        atomic_write_json(p, idx)


def _remove_from_index(ws_id, job_id):
    p = _index_path(ws_id)
    if not p.is_file():
        return
    with FileLock(p.with_name(p.name + ".lock")):
        try:
            idx = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _LOG.warning("jobs.store: corrupt index", exc_info=True)
            return
        ids = [item for item in list(idx.get("job_ids") or []) if item != job_id]
        idx["job_ids"] = ids
        idx["updated_at"] = now_iso()
        atomic_write_json(p, idx)

def _update_workspace_stats(ws_id):
    """Update workspace state with job counts."""
    try:
        from storage.workspace_store import update_workspace_state
        jobs = list_jobs(ws_id=ws_id, limit=500)
        status_counts = {}
        for j in jobs:
            s = j.get("status", "")
            status_counts[s] = status_counts.get(s, 0) + 1
        counts = {
            "jobs_count": len(jobs),
            "queued_jobs_count": status_counts.get("queued", 0),
            "running_jobs_count": status_counts.get("running", 0),
            "succeeded_jobs_count": status_counts.get("succeeded", 0),
            "failed_jobs_count": status_counts.get("failed", 0),
            "cancelled_jobs_count": status_counts.get("cancelled", 0),
            "last_job_id": jobs[0]["job_id"] if jobs else "",
            "recent_jobs": [{"job_id": j.get("job_id"), "job_type": j.get("job_type"),
                             "title": j.get("title"), "status": j.get("status"),
                             "updated_at": j.get("updated_at")} for j in jobs[:5]],
        }
        update_workspace_state(ws_id, {"job_stats": counts})
    except Exception:
        _LOG.warning("jobs.store: silent exception", exc_info=True)
