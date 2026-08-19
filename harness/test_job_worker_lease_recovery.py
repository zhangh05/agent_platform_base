from __future__ import annotations

import pytest


def test_reclaimed_running_job_is_fenced_without_reexecution(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    import jobs.worker as worker
    from jobs.queue import QueueReceipt
    from jobs.schemas import JobRecord
    from jobs.store import create_job, get_job

    job = create_job(JobRecord(
        job_id="job_leaseexpired",
        workspace_id="default",
        job_type="agent_run",
        status="running",
        payload={"message": "must not replay"},
    ))

    class ReclaimedQueue:
        def __init__(self):
            self.acked = []

        def reclaim_stale(self, _seconds):
            return 1

        def claim(self, _worker_id):
            return QueueReceipt("default", job.job_id, "receipt-2", attempt=2)

        def ack(self, receipt):
            self.acked.append(receipt)

        def retry(self, *_args):
            raise AssertionError("reclaimed running job must not be retried")

        def heartbeat(self, *_args):
            return True

    queue = ReclaimedQueue()
    executed = []
    monkeypatch.setattr("jobs.queue.get_job_queue", lambda: queue)
    monkeypatch.setattr("jobs.runner.run_job", lambda *_args: executed.append(True))

    outcome = worker.run_once()
    current = get_job("default", job.job_id)

    assert outcome["status"] == "lease_expired"
    assert executed == []
    assert queue.acked and queue.acked[0].attempt == 2
    assert current and current.status == "failed"
    unknown = current.metadata["active_turn"]["unknown_outcome"]
    assert unknown["error_code"] == "WORKER_LEASE_EXPIRED"
    assert unknown["execution_may_continue"] is True

    from jobs.manager import retry_job
    with pytest.raises(ValueError, match="unknown_outcome_requires_reconciliation"):
        retry_job("default", job.job_id)
