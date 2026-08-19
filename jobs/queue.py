"""Job queue contract for local and distributed worker implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Any


@dataclass(frozen=True)
class QueueReceipt:
    workspace_id: str
    job_id: str
    lease_id: str
    attempt: int
    principal: str = ""


class JobQueue(Protocol):
    def enqueue(self, workspace_id: str, job_id: str) -> QueueReceipt: ...
    def claim(self, worker_id: str) -> QueueReceipt | None: ...
    def ack(self, receipt: QueueReceipt) -> None: ...
    def retry(self, receipt: QueueReceipt, reason: str = "") -> None: ...
    def heartbeat(self, receipt: QueueReceipt, worker_id: str) -> bool: ...
    def reclaim_stale(self, max_age_seconds: int) -> int: ...
    def health(self) -> dict[str, Any]: ...


def queue_mode() -> str:
    import os
    return os.environ.get("LZCORE_QUEUE_MODE", "filesystem").strip().lower() or "filesystem"


def queue_configuration() -> dict[str, Any]:
    import os
    mode = queue_mode()
    configured = bool(os.environ.get("LZCORE_QUEUE_URL"))
    return {"mode": mode, "url_configured": configured, "distributed_ready": mode == "redis" and configured}


class FileJobQueue:
    def enqueue(self, workspace_id: str, job_id: str) -> QueueReceipt:
        return QueueReceipt(workspace_id, job_id, f"file:{job_id}", 1)

    def claim(self, worker_id: str) -> QueueReceipt | None:
        from jobs.store import get_next_queued_job
        from storage.principal import current_storage_principal, known_storage_principals, storage_principal
        principals = [current_storage_principal(), *known_storage_principals()]
        for principal in dict.fromkeys(principal for principal in principals if principal):
            with storage_principal(principal):
                job = get_next_queued_job()
            if job:
                return QueueReceipt(job.workspace_id, job.job_id, f"file:{job.job_id}", job.retry_count + 1, principal)
        return None

    def ack(self, receipt: QueueReceipt) -> None:
        return None

    def retry(self, receipt: QueueReceipt, reason: str = "") -> None:
        return None

    def heartbeat(self, receipt: QueueReceipt, worker_id: str) -> bool:
        return True

    def reclaim_stale(self, max_age_seconds: int) -> int:
        return 0

    def health(self) -> dict[str, Any]:
        return {"ok": True, "mode": "filesystem"}


class RedisJobQueue:
    QUEUED = "lzcore:jobs:queued"
    PROCESSING = "lzcore:jobs:processing"
    LEASES = "lzcore:jobs:leases"

    def __init__(self, url: str):
        import redis
        self.client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)

    @staticmethod
    def _payload(workspace_id: str, job_id: str, attempt: int = 1, principal: str = "") -> str:
        import json
        return json.dumps({"workspace_id": workspace_id, "job_id": job_id, "attempt": attempt, "principal": principal}, separators=(",", ":"))

    def enqueue(self, workspace_id: str, job_id: str) -> QueueReceipt:
        from storage.principal import current_storage_principal
        payload = self._payload(workspace_id, job_id, principal=current_storage_principal())
        self.client.lpush(self.QUEUED, payload)
        return QueueReceipt(workspace_id, job_id, payload, 1)

    def claim(self, worker_id: str) -> QueueReceipt | None:
        import json
        import time
        payload = self.client.rpoplpush(self.QUEUED, self.PROCESSING)
        if not payload:
            return None
        data = json.loads(payload)
        receipt = QueueReceipt(data["workspace_id"], data["job_id"], payload, int(data.get("attempt", 1)), str(data.get("principal") or ""))
        self.client.hset(self.LEASES, payload, json.dumps({"worker_id": worker_id, "heartbeat_at": time.time()}))
        return receipt

    def ack(self, receipt: QueueReceipt) -> None:
        self.client.lrem(self.PROCESSING, 1, receipt.lease_id)
        self.client.hdel(self.LEASES, receipt.lease_id)

    def retry(self, receipt: QueueReceipt, reason: str = "") -> None:
        self.ack(receipt)
        self.client.lpush(self.QUEUED, self._payload(receipt.workspace_id, receipt.job_id, receipt.attempt + 1, receipt.principal))

    def heartbeat(self, receipt: QueueReceipt, worker_id: str) -> bool:
        import json
        import time
        if not self.client.hexists(self.LEASES, receipt.lease_id):
            return False
        self.client.hset(self.LEASES, receipt.lease_id, json.dumps({"worker_id": worker_id, "heartbeat_at": time.time()}))
        return True

    def reclaim_stale(self, max_age_seconds: int) -> int:
        import json
        import time
        reclaimed = 0
        now = time.time()
        for payload, raw in self.client.hgetall(self.LEASES).items():
            try:
                lease = json.loads(raw)
                stale = now - float(lease.get("heartbeat_at") or 0) > max(1, max_age_seconds)
                data = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                stale = True
                data = {}
            if not stale:
                continue
            self.client.lrem(self.PROCESSING, 1, payload)
            self.client.hdel(self.LEASES, payload)
            if data.get("workspace_id") and data.get("job_id"):
                self.client.lpush(self.QUEUED, self._payload(data["workspace_id"], data["job_id"], int(data.get("attempt", 1)) + 1, str(data.get("principal") or "")))
                reclaimed += 1
        return reclaimed

    def health(self) -> dict[str, Any]:
        return {"ok": bool(self.client.ping()), "mode": "redis"}


def get_job_queue():
    import os
    mode = queue_mode()
    if mode == "redis":
        url = os.environ.get("LZCORE_QUEUE_URL", "").strip()
        if not url:
            raise RuntimeError("LZCORE_QUEUE_URL is required for redis queue")
        return RedisJobQueue(url)
    return FileJobQueue()
