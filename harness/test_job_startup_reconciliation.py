from __future__ import annotations

from jobs.schemas import JobRecord
from jobs.store import create_job, get_job, reconcile_running_jobs
from storage.principal import storage_principal


def test_startup_reconcile_closes_running_job_in_user_scoped_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setattr(
        "storage.principal.known_storage_principals",
        lambda: ["alice"],
    )
    with storage_principal("alice"):
        create_job(JobRecord(
            job_id="job_deadbeef",
            workspace_id="default",
            job_type="agent_run",
            status="running",
            updated_at="2024-01-01T00:00:00+00:00",
            metadata={
                "active_turn": {
                    "client_request_id": "request-restart",
                    "status": "running",
                    "stage": "turn_started",
                    "stage_label": "理解问题",
                },
            },
        ))

    reconciled = reconcile_running_jobs(
        finished_at="2024-01-02T00:00:00+00:00",
        started_before="2024-01-02T00:00:00+00:00",
    )

    assert reconciled == 1
    with storage_principal("alice"):
        record = get_job("default", "job_deadbeef")
    assert record is not None
    assert record.status == "failed"
    assert record.finished_at == "2024-01-02T00:00:00+00:00"
    assert record.error == "backend_restart_during_job"
    active = record.metadata["active_turn"]
    assert active["status"] == "failed"
    assert active["stage"] == "turn_failed"
    assert active["error"] == "backend_restart_during_job"
    assert record.progress["percent"] == 100
    assert record.progress["message"] == "处理失败"
