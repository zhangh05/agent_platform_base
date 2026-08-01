"""Job queue contract for local and distributed worker implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Any


@dataclass(frozen=True)
class QueueReceipt:
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
    return {"mode": mode, "url_configured": bool(os.environ.get("AGENT_PLATFORM_QUEUE_URL")), "distributed_ready": mode not in {"", "filesystem", "local"}}
