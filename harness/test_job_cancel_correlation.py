from flask import Flask

from jobs.schemas import JobRecord


def _running_turn(job_id: str = "job_cafebabe") -> JobRecord:
    return JobRecord(
        job_id=job_id,
        workspace_id="ws_cancel_guard",
        status="running",
        metadata={
            "active_turn": {
                "session_id": "session_cancel_guard",
                "client_request_id": "current-turn",
                "status": "running",
            },
        },
    )


def test_cancellation_guard_rejects_stale_active_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from jobs.store import create_job, get_job, request_job_cancellation

    record = _running_turn()
    create_job(record)

    rejected, applied = request_job_cancellation(
        record.workspace_id,
        record.job_id,
        expected_client_request_id="old-turn",
    )
    assert applied is False
    assert rejected.cancel_requested is False
    assert get_job(record.workspace_id, record.job_id).cancel_requested is False

    accepted, applied = request_job_cancellation(
        record.workspace_id,
        record.job_id,
        expected_client_request_id="current-turn",
    )
    assert applied is True
    assert accepted.cancel_requested is True
    assert get_job(record.workspace_id, record.job_id).cancel_requested is True


def test_cancel_route_returns_conflict_for_stale_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from backend.api.job_routes import register_job_routes
    from jobs.store import create_job, get_job

    record = _running_turn("job_deadbeef")
    create_job(record)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_job_routes(app)

    response = app.test_client().post(
        f"/api/jobs/{record.job_id}/cancel",
        json={
            "workspace_id": record.workspace_id,
            "client_request_id": "old-turn",
        },
    )
    assert response.status_code == 409
    assert response.get_json()["error"] == "stale_turn"
    assert get_job(record.workspace_id, record.job_id).cancel_requested is False
