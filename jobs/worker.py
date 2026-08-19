# jobs/worker.py
"""Local worker — poll queued jobs and execute them under a cross-platform lock."""

import logging
import os
import socket
import threading
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
    from jobs.runner import run_job
    from jobs.queue import get_job_queue

    lock_path = _lock_path()

    try:
        with FileLock(lock_path, timeout=0):
            queue_backend = get_job_queue()
            worker_id = os.getenv("LZCORE_WORKER_ID", "").strip() or f"{socket.gethostname()}:{os.getpid()}"
            lease_seconds = max(30, int(os.getenv("LZCORE_JOB_LEASE_SECONDS", "120")))
            reclaimed = queue_backend.reclaim_stale(lease_seconds)
            receipt = queue_backend.claim(worker_id)
            if not receipt:
                _write_state({"status": "idle", "message": "No queued jobs", "worker_id": worker_id, "reclaimed": reclaimed})
                return {"status": "idle", "message": "No queued jobs", "reclaimed": reclaimed}

            from jobs.store import get_job
            job = get_job(receipt.workspace_id, receipt.job_id)
            from jobs.store import fence_reclaimed_running_job
            fenced = fence_reclaimed_running_job(
                receipt.workspace_id, receipt.job_id,
                lease_id=receipt.lease_id, attempt=receipt.attempt,
            )
            if fenced is not None:
                queue_backend.ack(receipt)
                _write_state({"status": "lease_expired", "job_id": fenced.job_id, "job_type": fenced.job_type, "worker_id": worker_id, "attempt": receipt.attempt})
                return {"status": "lease_expired", "job_id": fenced.job_id}
            if not job:
                queue_backend.ack(receipt)
                return {"status": "missing", "job_id": receipt.job_id}
            state = {"status": "running", "job_id": job.job_id, "job_type": job.job_type, "worker_id": worker_id, "attempt": receipt.attempt}
            _write_state(state)
            heartbeat_stop = threading.Event()
            heartbeat = threading.Thread(
                target=_heartbeat_loop,
                args=(queue_backend, receipt, worker_id, heartbeat_stop, state, max(5, lease_seconds // 3)),
                daemon=True,
                name="job-lease-heartbeat",
            )
            heartbeat.start()
            try:
                run_job(job.workspace_id, job.job_id)
            except Exception:
                heartbeat_stop.set()
                heartbeat.join(timeout=1)
                queue_backend.retry(receipt, "worker_error")
                raise
            else:
                heartbeat_stop.set()
                heartbeat.join(timeout=1)
                queue_backend.ack(receipt)
            _write_state({"status": "completed", "job_id": job.job_id, "job_type": job.job_type})
            return {"status": "completed", "job_id": job.job_id}
    except TimeoutError:
        return {"status": "locked", "message": "Another worker is running"}


def get_worker_state() -> dict:
    from storage.runtime_state_store import read_runtime_record

    state = read_runtime_record("jobs_worker_state") or {"status": "idle"}
    updated_at = str(state.get("updated_at") or "")
    if updated_at:
        try:
            from storage.time_utils import from_iso
            age = max(0.0, time.time() - from_iso(updated_at))
            state["heartbeat_age_seconds"] = round(age, 2)
            stale_after = max(30, int(os.getenv("LZCORE_WORKER_STALE_SECONDS", "180")))
            state["healthy"] = not (state.get("status") == "running" and age > stale_after)
            if not state["healthy"]:
                state["status"] = "stale"
        except ValueError:
            state["healthy"] = False
    return state


def _write_state(state):
    from storage.runtime_state_store import save_runtime_record

    state["updated_at"] = now_iso()
    save_runtime_record("jobs_worker_state", state)


def _heartbeat_loop(queue_backend, receipt, worker_id, stop_event, state, interval):
    while not stop_event.wait(interval):
        if not queue_backend.heartbeat(receipt, worker_id):
            _LOG.error("job lease was lost: %s", receipt.job_id)
            return
        _write_state(dict(state))
