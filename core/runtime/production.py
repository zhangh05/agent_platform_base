"""Production dependency readiness without exposing connection details."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Callable


def _probe(name: str, mode: str, callback: Callable[[], Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        details = callback()
        return {"name": name, "mode": mode, "status": "ok", "latency_ms": round((time.monotonic() - started) * 1000, 2), "details": details if isinstance(details, dict) else {}}
    except Exception as exc:
        return {"name": name, "mode": mode, "status": "error", "latency_ms": round((time.monotonic() - started) * 1000, 2), "message": str(exc)[:160], "details": {}}


def _storage_probe() -> dict[str, Any]:
    from storage.backend import backend_mode, get_record_backend, validate_backend_configuration
    errors = validate_backend_configuration()
    if errors:
        raise RuntimeError("; ".join(errors))
    mode = backend_mode()
    if mode in {"postgres", "postgresql"}:
        backend = get_record_backend()
        return backend.health()
    from storage.paths import runtime_root
    root = runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".readiness"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)
    return {"root_writable": True}


def _object_probe() -> dict[str, Any]:
    from storage.object_store import get_object_store
    return get_object_store().health()


def _queue_probe() -> dict[str, Any]:
    from jobs.queue import get_job_queue
    return get_job_queue().health()


def production_readiness() -> dict[str, Any]:
    from jobs.queue import queue_mode
    from storage.backend import backend_mode
    from storage.object_store import object_store_mode
    from jobs.worker import get_worker_state
    components = [
        _probe("record_storage", backend_mode(), _storage_probe),
        _probe("object_storage", object_store_mode(), _object_probe),
        _probe("job_queue", queue_mode(), _queue_probe),
    ]
    worker = get_worker_state()
    worker_ok = worker.get("healthy", True) is not False
    components.append({
        "name": "job_worker",
        "mode": "distributed" if queue_mode() == "redis" else "local",
        "status": "ok" if worker_ok else "error",
        "latency_ms": 0,
        "details": {key: worker.get(key) for key in ("status", "worker_id", "heartbeat_age_seconds") if key in worker},
    })
    return {
        "ready": all(item["status"] == "ok" for item in components),
        "components": components,
        "checked_at": time.time(),
    }
