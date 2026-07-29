"""Recent run summaries should reflect redacted trace facts."""

import json
from pathlib import Path

import pytest

try:
    from backend.main import app as _flask_app
except ImportError:
    _flask_app = None


@pytest.fixture
def client(temp_dirs):
    if _flask_app is None:
        pytest.skip("Flask app not importable")
    _flask_app.config["TESTING"] = True
    return _flask_app.test_client()


def test_recent_runs_enriches_counts_from_trace(client, temp_dirs):
    from observability.schemas import TraceEvent, TraceRecord
    from observability.store import write_trace
    from storage.workspace_store import ensure_workspace

    ws_id = "runs_trace_summary_ws"
    run_id = "run-trace-summary"
    trace_id = "trace-trace-summary"
    ensure_workspace(ws_id)

    runs_dir = Path(str(temp_dirs["workspace_dir"])) / ws_id / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "workspace_id": ws_id,
                "session_id": "",
                "created_at": "2026-06-19T01:00:00",
                "user_input_summary": "查看IP地址，使用shell",
                "status": "ok",
                "trace_id": trace_id,
                "tool_call_count": 0,
                "warning_count": 0,
                "error_count": 0,
            },
            ensure_ascii=False,
        )
    )

    trace = TraceRecord(
        trace_id=trace_id,
        run_id=run_id,
        workspace_id=ws_id,
        status="success",
        events=[
            TraceEvent(
                trace_id=trace_id,
                run_id=run_id,
                event_type="run_started",
                timestamp="2026-06-19T01:00:01",
            ).as_dict(),
            TraceEvent(
                trace_id=trace_id,
                run_id=run_id,
                event_type="tool_call_started",
                metadata={"canonical_tool_id": "exec.run"},
                timestamp="2026-06-19T01:00:02",
            ).as_dict(),
            TraceEvent(
                trace_id=trace_id,
                run_id=run_id,
                event_type="warning",
                timestamp="2026-06-19T01:00:03",
            ).as_dict(),
            TraceEvent(
                trace_id=trace_id,
                run_id=run_id,
                event_type="tool_call_failed",
                metadata={"canonical_tool_id": "exec.run"},
                timestamp="2026-06-19T01:00:04",
            ).as_dict(),
        ],
    )
    write_trace(trace, ws_id)

    resp = client.get(f"/api/runs/recent?workspace_id={ws_id}&session_status=")
    assert resp.status_code == 200
    run = resp.get_json()["runs"][0]
    assert run["run_id"] == run_id
    assert run["tool_call_count"] == 1
    assert run["warning_count"] == 1
    assert run["error_count"] == 1
    assert run["started_at"] == "2026-06-19T01:00:00"
    assert run["finished_at"] == "2026-06-19T01:00:04"
    assert run["visible_tools"] == ["exec.run"]
    assert run["event_count"] == 4
