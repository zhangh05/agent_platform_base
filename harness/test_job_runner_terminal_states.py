from __future__ import annotations

from jobs.schemas import JobRecord


def test_runner_does_not_overwrite_failed_terminal_state(monkeypatch):
    import jobs.runner as runner

    record = JobRecord(job_id="job_deadbeef", workspace_id="default", job_type="export_report", status="queued")
    monkeypatch.setattr(runner, "get_job", lambda *_args: record)
    monkeypatch.setattr(runner, "mark_running", lambda *_args: setattr(record, "status", "running"))
    monkeypatch.setattr(runner, "_run_export_report", lambda _record: setattr(record, "status", "failed"))
    succeeded = []
    monkeypatch.setattr(runner, "mark_succeeded", lambda *_args, **_kwargs: succeeded.append(True))

    runner.run_job(record.workspace_id, record.job_id)
    assert record.status == "failed"
    assert succeeded == []


def test_knowledge_job_calls_real_reindex_service(monkeypatch):
    import agent.modules.knowledge.service as knowledge_service
    import jobs.runner as runner

    record = JobRecord(
        job_id="job_cafebabe",
        workspace_id="default",
        job_type="knowledge_index",
        status="running",
        payload={"source_id": "ksrc_test"},
    )
    calls = []
    monkeypatch.setattr(knowledge_service, "reindex_source", lambda ws, source: calls.append((ws, source)) or {"ok": True, "source_id": source, "chunk_count": 3})
    monkeypatch.setattr(runner, "update_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "update_job", lambda *_args, **_kwargs: record)

    runner._run_knowledge_index(record)
    assert calls == [("default", "ksrc_test")]


def test_cancel_check_transitions_running_job(monkeypatch):
    import jobs.runner as runner

    record = JobRecord(job_id="job_1234abcd", workspace_id="default", status="running", cancel_requested=True)
    cancelled = []
    monkeypatch.setattr("jobs.store.get_job", lambda *_args: record)
    monkeypatch.setattr(runner, "mark_cancelled", lambda ws, job: cancelled.append((ws, job)))

    assert runner._cancel_check(record) is True
    assert cancelled == [("default", "job_1234abcd")]


def test_mark_failed_has_no_retired_memory_side_channel(monkeypatch):
    import jobs.manager as manager

    record = JobRecord(job_id="job_deadf00d", workspace_id="default", status="running")
    monkeypatch.setattr(manager, "get_job", lambda *_args: record)
    monkeypatch.setattr(manager, "update_job", lambda *_args, **_kwargs: record)
    monkeypatch.setattr(manager, "append_event", lambda *_args, **_kwargs: None)

    assert manager.mark_failed("default", record.job_id, "failed") is record
