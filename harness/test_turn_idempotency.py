import multiprocessing
from datetime import datetime, timedelta, timezone
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
        "ws-idempotent", "session-idempotent", "first input",
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
        "ws-idempotent", "session-idempotent", "first input",
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


def test_cancelled_claim_is_not_misrecorded_as_failed(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    import jobs.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_begin_session_turn_unlocked", lambda *_args, **_kwargs: "job-cancelled")
    lifecycle.claim_session_turn(
        "ws-cancelled", "session-cancelled", "cancel me",
        client_request_id="request-cancelled",
    )

    class CancelledJob:
        cancel_requested = True
        status = "cancelled"

    monkeypatch.setattr(lifecycle, "get_job", lambda *_args, **_kwargs: CancelledJob())
    lifecycle.finish_claimed_session_turn(
        "ws-cancelled", "session-cancelled",
        client_request_id="request-cancelled",
        job_id="job-cancelled",
        ok=False,
        error="cancelled_by_user",
    )

    claim = lifecycle.claim_session_turn(
        "ws-cancelled", "session-cancelled", "cancel me",
        client_request_id="request-cancelled",
    )
    assert claim.should_execute is False
    assert claim.status == "cancelled"
    assert claim.error == "任务已取消。"


def test_permanent_session_delete_removes_turn_request_registry(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    import jobs.lifecycle as lifecycle
    from storage.session_store import delete_session_permanently, ensure_session

    session_id = "session-delete-registry"
    ws_id = "ws-delete-registry"
    ensure_session(session_id, ws_id)
    monkeypatch.setattr(lifecycle, "_begin_session_turn_unlocked", lambda *_args, **_kwargs: "job-delete-registry")
    lifecycle.claim_session_turn(
        ws_id, session_id, "delete registry fixture",
        client_request_id="request-delete-registry",
    )
    registry_dir = tmp_path / ws_id / "sys" / "request_registry" / session_id
    assert registry_dir.is_dir()

    assert delete_session_permanently(session_id, ws_id, True) is True
    assert not registry_dir.exists()



def test_different_request_id_cannot_replace_running_session_turn(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    import jobs.lifecycle as lifecycle
    from storage.session_store import ensure_session

    ws_id = "ws-single-flight"
    session_id = "session-single-flight"
    ensure_session(session_id, ws_id)
    first = lifecycle.claim_session_turn(
        ws_id, session_id, "first",
        client_request_id="request-a",
    )
    second = lifecycle.claim_session_turn(
        ws_id, session_id, "second",
        client_request_id="request-b",
    )

    assert first.should_execute is True
    assert second.should_execute is False
    assert second.status == "conflict"
    assert second.error == "session_turn_in_progress"


def test_same_request_reconciles_to_failed_after_interrupted_job(monkeypatch, tmp_path: Path):
    """A backend-startup failure must not leave the idempotency key running."""
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    import jobs.lifecycle as lifecycle
    from jobs.store import update_job
    from storage.session_store import ensure_session

    ws_id = "ws-restart-reconcile"
    session_id = "session-restart-reconcile"
    request_id = "request-restart-reconcile"
    ensure_session(session_id, ws_id)
    first = lifecycle.claim_session_turn(
        ws_id, session_id, "interrupted request", client_request_id=request_id,
    )
    assert first.should_execute is True
    update_job(ws_id, first.job_id, {
        "status": "failed",
        "error": "backend_restart_during_job",
    })

    duplicate = lifecycle.claim_session_turn(
        ws_id, session_id, "interrupted request", client_request_id=request_id,
    )
    assert duplicate.should_execute is False
    assert duplicate.job_id == first.job_id
    assert duplicate.status == "failed"
    assert duplicate.error == "backend_restart_during_job"

    record = lifecycle._read_request_record(
        lifecycle._request_registry_path(ws_id, session_id, request_id),
    )
    assert record["status"] == "failed"
    assert record["error"] == "backend_restart_during_job"
    assert record["finished_at"]


def test_request_registry_prunes_expired_and_excess_terminal_records(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    import jobs.lifecycle as lifecycle

    ws_id = "ws-registry-retention"
    session_id = "session-registry-retention"
    monkeypatch.setattr(lifecycle, "_REQUEST_REGISTRY_TERMINAL_TTL_SECONDS", 60)
    monkeypatch.setattr(lifecycle, "_REQUEST_REGISTRY_MAX_TERMINAL_RECORDS", 2)

    now = datetime.now(timezone.utc)
    records = [
        ("expired", now - timedelta(seconds=61)),
        ("oldest-kept", now - timedelta(seconds=3)),
        ("middle-kept", now - timedelta(seconds=2)),
        ("newest-kept", now - timedelta(seconds=1)),
    ]
    for request_id, timestamp in records:
        lifecycle._write_request_record(
            lifecycle._request_registry_path(ws_id, session_id, request_id),
            {
                "client_request_id": request_id,
                "session_id": session_id,
                "workspace_id": ws_id,
                "job_id": "",
                "status": "succeeded",
                "created_at": timestamp.isoformat(),
                "updated_at": timestamp.isoformat(),
                "finished_at": timestamp.isoformat(),
                "run_id": "",
                "trace_id": "",
                "error": "",
            },
        )

    lifecycle._prune_request_registry_unlocked(ws_id, session_id)
    registry_dir = lifecycle._request_registry_path(ws_id, session_id, "probe").parent
    surviving = {
        lifecycle._read_request_record(path)["client_request_id"]
        for path in registry_dir.glob("*.json")
    }
    assert surviving == {"middle-kept", "newest-kept"}


def test_running_old_request_never_inherits_later_turn_terminal_status(monkeypatch):
    import jobs.lifecycle as lifecycle

    class LaterTurnJob:
        status = "succeeded"
        cancel_requested = False
        finished_at = "2026-08-17T00:00:00+00:00"
        error = ""
        metadata = {"active_turn": {"client_request_id": "later-request"}}

    monkeypatch.setattr(lifecycle, "get_job", lambda *_args, **_kwargs: LaterTurnJob())
    record = {
        "job_id": "reused-job",
        "status": "running",
        "client_request_id": "old-request",
    }
    assert lifecycle._reconcile_running_request_record("ws", "old-request", record) is True
    assert record["status"] == "failed"
    assert record["error"] == "turn_execution_interrupted"


def test_claim_persists_user_message_before_runtime_completion(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    import jobs.lifecycle as lifecycle
    from storage.session_store import ensure_session, get_session_messages

    ws_id = "ws-interrupted-message"
    session_id = "session-interrupted-message"
    request_id = "request-interrupted-message"
    user_input = "服务中断后，这条已接受的输入仍应可恢复。"
    ensure_session(session_id, ws_id)

    claim = lifecycle.claim_session_turn(
        ws_id, session_id, user_input, client_request_id=request_id,
    )
    assert claim.should_execute is True

    messages = get_session_messages(session_id, ws_id)
    user_messages = [message for message in messages if message["role"] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == user_input
    assert user_messages[0]["metadata"]["client_request_id"] == request_id
    assert user_messages[0]["metadata"]["provisional"] is True


def test_terminal_turn_reuses_claimed_user_message_without_run_fallback_duplicate(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from types import SimpleNamespace
    from agent.protocol.op import AgentOp
    from agent.runtime.result import AgentResult
    from agent.runtime.turn_persistence import persist_run_record
    from storage.session_store import ensure_session, get_session_messages

    ws_id = "ws-message-dedup"
    session_id = "session-message-dedup"
    request_id = "request-message-dedup"
    user_input = "请在完成后仍只保留一条用户输入。"
    ensure_session(session_id, ws_id)

    import jobs.lifecycle as lifecycle
    claim = lifecycle.claim_session_turn(
        ws_id, session_id, user_input, client_request_id=request_id,
    )
    assert claim.should_execute is True

    turn = SimpleNamespace(
        turn_id="run-message-dedup",
        op=AgentOp.user_message(
            user_input=user_input,
            session_id=session_id,
            workspace_id=ws_id,
            metadata={"client_request_id": request_id},
        ),
        context={},
    )
    session = SimpleNamespace(
        session_id=session_id,
        workspace_id=ws_id,
        is_sub_agent=False,
    )
    result = AgentResult(
        ok=True,
        final_response="已完成并持久化。",
        session_id=session_id,
        turn_id=turn.turn_id,
        trace_id="trace-message-dedup",
    )
    persist_run_record(session, turn, result, SimpleNamespace(metadata={}))

    messages = get_session_messages(session_id, ws_id)
    user_messages = [message for message in messages if message["role"] == "user"]
    assistant_messages = [message for message in messages if message["role"] == "assistant"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == user_input
    assert user_messages[0]["metadata"]["client_request_id"] == request_id
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["content"] == result.final_response


def test_same_request_id_with_different_input_is_rejected(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    import jobs.lifecycle as lifecycle
    from storage.session_store import ensure_session

    ws_id = "ws-request-payload-binding"
    session_id = "session-request-payload-binding"
    request_id = "request-payload-binding"
    ensure_session(session_id, ws_id)
    first = lifecycle.claim_session_turn(
        ws_id, session_id, "first immutable request", client_request_id=request_id,
    )
    assert first.should_execute is True
    conflict = lifecycle.claim_session_turn(
        ws_id, session_id, "different request must not inherit first result",
        client_request_id=request_id,
    )
    assert conflict.should_execute is False
    assert conflict.job_id == first.job_id
    assert conflict.status == "conflict"
    assert conflict.error == "client_request_payload_mismatch"
    record = lifecycle._read_request_record(
        lifecycle._request_registry_path(ws_id, session_id, request_id),
    )
    assert record["input_sha256"] == lifecycle._request_input_sha256("first immutable request")
