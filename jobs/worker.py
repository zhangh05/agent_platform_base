# jobs/worker.py
"""Local worker — poll queued jobs and execute them under a cross-platform lock."""

import logging
import time

from storage.time_utils import now_iso
from storage.locking import FileLock
from storage.runtime_state_store import job_worker_lock_path

_worker_active = False
_LOG = logging.getLogger(__name__)


def _runtime_dir():
    return job_worker_lock_path().parent


def _lock_path():
    return job_worker_lock_path()


def start_worker(poll_interval=1.0):
    global _worker_active
    _worker_active = True
    _runtime_dir()
    while _worker_active:
        try:
            run_once()
        except Exception:
            _LOG.exception("job worker iteration failed")
        time.sleep(poll_interval)


def stop_worker():
    global _worker_active
    _worker_active = False


def run_once() -> dict:
    """Poll and execute one queued job. Returns result."""
    from jobs.store import get_next_queued_job
    from jobs.runner import run_job

    lock_path = _lock_path()

    try:
        with FileLock(lock_path, timeout=0):
            job = get_next_queued_job()
            if not job:
                _write_state({"status": "idle", "message": "No queued jobs"})
                return {"status": "idle", "message": "No queued jobs"}

            _write_state({"status": "running", "job_id": job.job_id, "job_type": job.job_type})
            run_job(job.workspace_id, job.job_id)
            _write_state({"status": "completed", "job_id": job.job_id, "job_type": job.job_type})
            return {"status": "completed", "job_id": job.job_id}
    except TimeoutError:
        return {"status": "locked", "message": "Another worker is running"}


def get_worker_state() -> dict:
    from storage.runtime_state_store import read_runtime_record

    return read_runtime_record("jobs_worker_state") or {"status": "idle"}


def _write_state(state):
    from storage.runtime_state_store import save_runtime_record

    state["updated_at"] = now_iso()
    save_runtime_record("jobs_worker_state", state)
