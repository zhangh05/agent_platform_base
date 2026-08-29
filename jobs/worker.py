# jobs/worker.py
"""Local worker — poll queued jobs and execute them under a cross-platform lock."""

import logging
import os
import socket
import threading
import time
import hashlib
import json
from contextlib import nullcontext

from storage.time_utils import now_iso
from storage.locking import FileLock
from storage.runtime_state_store import job_worker_lock_path

_worker_active = False
_LOG = logging.getLogger(__name__)


def _worker_id() -> str:
    return os.getenv("LZCORE_WORKER_ID", "").strip() or f"{socket.gethostname()}:{os.getpid()}"


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

    # Filesystem queues need a host lock around claim+execution. Redis already
    # provides leases and fencing; retaining the file lock there silently
    # serialized every worker and defeated horizontal capacity.
    mode = str(os.getenv("LZCORE_QUEUE_MODE", "filesystem") or "filesystem").strip().lower()
    iteration_lock = FileLock(_lock_path(), timeout=0) if mode in {"filesystem", "file", "local"} else nullcontext()

    try:
        with iteration_lock:
            queue_backend = get_job_queue()
            worker_id = _worker_id()
            lease_seconds = max(30, int(os.getenv("LZCORE_JOB_LEASE_SECONDS", "120")))
            reclaimed = queue_backend.reclaim_stale(lease_seconds)
            receipt = queue_backend.claim(worker_id)
            if not receipt:
                _write_state({"status": "idle", "message": "No queued jobs", "worker_id": worker_id, "reclaimed": reclaimed})
                return {"status": "idle", "message": "No queued jobs", "reclaimed": reclaimed}

            from storage.principal import storage_principal
            principal_scope = lambda: storage_principal(receipt.principal) if receipt.principal else nullcontext()
            with principal_scope():
                from jobs.store import fence_reclaimed_running_job, get_job
                job = get_job(receipt.workspace_id, receipt.job_id)
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
                with principal_scope():
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
            _write_state({"status": "completed", "job_id": job.job_id, "job_type": job.job_type, "worker_id": worker_id})
            return {"status": "completed", "job_id": job.job_id}
    except TimeoutError:
        return {"status": "locked", "message": "Another worker is running"}


def get_worker_state() -> dict:
    from storage.runtime_state_store import read_runtime_record

    states: list[dict] = []
    if os.getenv("LZCORE_QUEUE_MODE", "filesystem").strip().lower() == "redis":
        try:
            import redis
            url = os.environ.get("LZCORE_QUEUE_URL") or os.environ.get("LZCORE_REDIS_URL", "")
            client = redis.Redis.from_url(url, decode_responses=True)
            for key in client.scan_iter(match="lzcore:worker_state:*"):
                raw = client.get(key)
                value = json.loads(raw or "{}")
                if isinstance(value, dict):
                    states.append(value)
        except Exception:  # noqa: BLE001 - diagnostic Redis reads fall back to the durable runtime record
            _LOG.warning("unable to read Redis worker states", exc_info=True)
    if not states:
        state = read_runtime_record("jobs_worker_state") or {"status": "idle"}
        states = [state]
    for state in states:
        _annotate_worker_health(state)
    states.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    state = dict(states[0])
    if len(states) > 1:
        state["worker_count"] = len(states)
        state["healthy_worker_count"] = sum(1 for item in states if item.get("healthy", True) is not False)
        state["workers"] = states
        state["healthy"] = state["healthy_worker_count"] > 0
    return state


def _annotate_worker_health(state: dict) -> None:
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


def _write_state(state):
    from storage.runtime_state_store import save_runtime_record

    state.setdefault("worker_id", _worker_id())
    state["updated_at"] = now_iso()
    if os.getenv("LZCORE_QUEUE_MODE", "filesystem").strip().lower() == "redis":
        try:
            import redis
            url = os.environ.get("LZCORE_QUEUE_URL") or os.environ.get("LZCORE_REDIS_URL", "")
            client = redis.Redis.from_url(url, decode_responses=True)
            digest = hashlib.sha256(str(state["worker_id"]).encode("utf-8")).hexdigest()[:24]
            client.set(f"lzcore:worker_state:{digest}", json.dumps(state, ensure_ascii=False), ex=600)
            return
        except Exception:  # noqa: BLE001 - Redis publication failure must not terminate the worker
            _LOG.warning("unable to publish Redis worker state", exc_info=True)
    save_runtime_record("jobs_worker_state", state)


def _heartbeat_loop(queue_backend, receipt, worker_id, stop_event, state, interval):
    while not stop_event.wait(interval):
        if not queue_backend.heartbeat(receipt, worker_id):
            _LOG.error("job lease was lost: %s", receipt.job_id)
            return
        _write_state(dict(state))
