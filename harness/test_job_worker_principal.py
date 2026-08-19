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
