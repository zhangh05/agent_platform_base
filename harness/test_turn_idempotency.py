import multiprocessing
import os

from pathlib import Path


def test_client_request_claim_is_durable_and_terminal(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    import jobs.lifecycle as lifecycle

    started = {"count": 0}

    def fake_begin(*_args, **_kwargs):
        started["count"] += 1
        return "job-idempotent"

    monkeypatch.setattr(lifecycle, "_begin_session_turn_unlocked", fake_begin)

    first = lifecycle.claim_session_turn(
        "ws-idempotent", "session-idempotent", "first input",
        client_request_id="client-request-idempotent",
    )
    duplicate = lifecycle.claim_session_turn(
        "ws-idempotent", "session-idempotent", "changed input",
        client_request_id="client-request-idempotent",
    )

    assert first.should_execute is True
    assert duplicate.should_execute is False
    assert duplicate.status == "running"
    assert duplicate.job_id == "job-idempotent"
    assert started["count"] == 1

    lifecycle.finish_claimed_session_turn(
        "ws-idempotent", "session-idempotent",
        client_request_id="client-request-idempotent",
        job_id="job-idempotent",
        run_id="run-idempotent",
        trace_id="trace-idempotent",
        ok=True,
    )
    completed = lifecycle.claim_session_turn(
        "ws-idempotent", "session-idempotent", "retry input",
        client_request_id="client-request-idempotent",
    )

    assert completed.should_execute is False
    assert completed.status == "succeeded"
    assert completed.run_id == "run-idempotent"
    assert completed.trace_id == "trace-idempotent"
    assert started["count"] == 1


def test_http_duplicate_client_request_skips_agent_execution(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from flask import Flask
    from backend.api import agent_routes
    import agent.app.service as service
    import jobs.lifecycle as lifecycle

    calls = {"count": 0}

    class FakeResult:
        def to_dict(self):
            return {
                "ok": True,
                "final_response": "first HTTP answer",
                "session_id": "session-http-idempotent",
                "turn_id": "run-http-idempotent",
                "trace_id": "trace-http-idempotent",
                "events": [],
                "tool_calls": [],
                "metadata": {},
                "warnings": [],
                "errors": [],
            }

    class FakeApp:
        def submit_user_message(self, **_kwargs):
            calls["count"] += 1
            return FakeResult()

    monkeypatch.setattr(service, "get_default_agent_app", lambda: FakeApp())
    monkeypatch.setattr(agent_routes, "_normalize_agent_result", lambda result, _ws_id: result)
    monkeypatch.setattr(lifecycle, "_begin_session_turn_unlocked", lambda *_args, **_kwargs: "job-http-idempotent")

    app = Flask(__name__)
    payload = {
        "message": "same HTTP request",
        "workspace_id": "ws-http-idempotent",
        "session_id": "session-http-idempotent",
        "metadata": {"client_request_id": "request-http-idempotent"},
    }
    with app.test_request_context("/api/agent/message", method="POST", json=payload):
        first = agent_routes.agent_message()
    with app.test_request_context("/api/agent/message", method="POST", json=payload):
        second = agent_routes.agent_message()

    assert calls["count"] == 1
    duplicate_response, status = second
    assert status == 200
    duplicate = duplicate_response.get_json()
    assert duplicate["metadata"]["idempotent"] is True
    assert duplicate["metadata"]["idempotent_redirect"] == {
        "job_id": "job-http-idempotent", "status": "succeeded",
    }


def test_metadata_normalization_preserves_client_request_id_only_as_correlation_data():
    from backend.core.agent_contract import normalize_metadata

    metadata = normalize_metadata(
        {
            "client_request_id": "request-contract-id",
            "runtime_guidance": "must remain server-owned",
        },
        transport="http",
        stream_mode="sync",
    )

    assert metadata["client_request_id"] == "request-contract-id"
    assert "runtime_guidance" not in metadata
    assert metadata["transport"] == "http"


def _claim_request_in_process(root: str, output) -> None:
    os.environ["LZCORE_WORKSPACE_ROOT"] = root
    from jobs.lifecycle import claim_session_turn

    claim = claim_session_turn(
        "ws-process-idempotent",
        "session-process-idempotent",
        "same concurrent request",
        client_request_id="request-process-idempotent",
    )
    output.put((claim.should_execute, claim.job_id, claim.status))


def test_client_request_claim_allows_one_cross_process_executor(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    processes = [
        context.Process(target=_claim_request_in_process, args=(str(tmp_path), output))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    claims = [output.get(timeout=2) for _ in processes]
    assert sum(1 for should_execute, *_ in claims if should_execute) == 1
    assert all(job_id for _, job_id, _ in claims)
    assert any(status == "running" for should_execute, _job_id, status in claims if not should_execute)
