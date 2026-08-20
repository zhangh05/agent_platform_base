from __future__ import annotations


def test_file_worker_claims_user_scoped_job_with_owner_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from jobs.manager import create_job
    from jobs.queue import FileJobQueue
    from storage.principal import storage_principal
    monkeypatch.setattr("storage.principal.known_storage_principals", lambda: ["alice"])

    with storage_principal("alice"):
        job = create_job(
            workspace_id="default",
            job_type="agent_run",
            title="alice queued job",
            payload={"message": "run as alice"},
            enqueue=True,
        )

    receipt = FileJobQueue().claim("worker-a")

    assert receipt is not None
    assert receipt.job_id == job.job_id
    assert receipt.principal == "alice"


def test_worker_runs_user_scoped_job_under_creator_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr("storage.principal.known_storage_principals", lambda: ["alice"])
    from jobs.manager import create_job
    import jobs.worker as worker
    import jobs.runner as runner
    from storage.principal import current_storage_principal, storage_principal

    with storage_principal("alice"):
        job = create_job(
            workspace_id="default",
            job_type="agent_run",
            title="alice background job",
            payload={"message": "run under owner"},
            enqueue=True,
        )

    observed = []
    monkeypatch.setattr(runner, "run_job", lambda ws, jid: observed.append((current_storage_principal(), ws, jid)))

    outcome = worker.run_once()

    assert outcome == {"status": "completed", "job_id": job.job_id}
    assert observed == [("alice", "default", job.job_id)]


def test_worker_fences_reclaimed_user_job_under_creator_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr("storage.principal.known_storage_principals", lambda: ["alice"])

    import jobs.worker as worker
    from jobs.queue import QueueReceipt
    from jobs.schemas import JobRecord
    from jobs.store import create_job, get_job
    from storage.principal import storage_principal

    with storage_principal("alice"):
        job = create_job(JobRecord(
            job_id="job_aliceleaseexpired",
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
            return QueueReceipt("default", job.job_id, "receipt-alice-2", attempt=2, principal="alice")

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
    with storage_principal("alice"):
        current = get_job("default", job.job_id)

    assert outcome == {"status": "lease_expired", "job_id": job.job_id}
    assert executed == []
    assert queue.acked and queue.acked[0].principal == "alice"
    assert current and current.status == "failed"
    assert current.metadata["active_turn"]["unknown_outcome"]["error_code"] == "WORKER_LEASE_EXPIRED"
