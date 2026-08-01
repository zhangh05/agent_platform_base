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


class JobQueue(Protocol):
    def enqueue(self, workspace_id: str, job_id: str) -> QueueReceipt: ...
    def claim(self, worker_id: str) -> QueueReceipt | None: ...
    def ack(self, receipt: QueueReceipt) -> None: ...
    def retry(self, receipt: QueueReceipt, reason: str = "") -> None: ...


def queue_mode() -> str:
    import os
    return os.environ.get("AGENT_PLATFORM_QUEUE_MODE", "filesystem").strip().lower() or "filesystem"


def queue_configuration() -> dict[str, Any]:
    import os
    mode = queue_mode()
    configured = bool(os.environ.get("AGENT_PLATFORM_QUEUE_URL"))
    return {"mode": mode, "url_configured": configured, "distributed_ready": mode == "redis" and configured}


class FileJobQueue:
    def enqueue(self, workspace_id: str, job_id: str) -> QueueReceipt:
        return QueueReceipt(workspace_id, job_id, f"file:{job_id}", 1)

    def claim(self, worker_id: str) -> QueueReceipt | None:
        from jobs.store import get_next_queued_job
        job = get_next_queued_job()
        return QueueReceipt(job.workspace_id, job.job_id, f"file:{job.job_id}", job.retry_count + 1) if job else None

    def ack(self, receipt: QueueReceipt) -> None:
        return None

    def retry(self, receipt: QueueReceipt, reason: str = "") -> None:
        return None


class RedisJobQueue:
    QUEUED = "agent-platform:jobs:queued"
    PROCESSING = "agent-platform:jobs:processing"

    def __init__(self, url: str):
        import redis
        self.client = redis.Redis.from_url(url, decode_responses=True)

    @staticmethod
    def _payload(workspace_id: str, job_id: str, attempt: int = 1) -> str:
        import json
        return json.dumps({"workspace_id": workspace_id, "job_id": job_id, "attempt": attempt}, separators=(",", ":"))

    def enqueue(self, workspace_id: str, job_id: str) -> QueueReceipt:
        payload = self._payload(workspace_id, job_id)
        self.client.lpush(self.QUEUED, payload)
        return QueueReceipt(workspace_id, job_id, payload, 1)

    def claim(self, worker_id: str) -> QueueReceipt | None:
        import json
        payload = self.client.rpoplpush(self.QUEUED, self.PROCESSING)
        if not payload:
            return None
        data = json.loads(payload)
        return QueueReceipt(data["workspace_id"], data["job_id"], payload, int(data.get("attempt", 1)))

    def ack(self, receipt: QueueReceipt) -> None:
        self.client.lrem(self.PROCESSING, 1, receipt.lease_id)

    def retry(self, receipt: QueueReceipt, reason: str = "") -> None:
        self.ack(receipt)
        self.client.lpush(self.QUEUED, self._payload(receipt.workspace_id, receipt.job_id, receipt.attempt + 1))


def get_job_queue():
    import os
    mode = queue_mode()
    if mode == "redis":
        url = os.environ.get("AGENT_PLATFORM_QUEUE_URL", "").strip()
        if not url:
            raise RuntimeError("AGENT_PLATFORM_QUEUE_URL is required for redis queue")
        return RedisJobQueue(url)
    return FileJobQueue()
