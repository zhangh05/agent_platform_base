"""Focused contracts for restart-safe ordinary Agent approvals."""

import asyncio
from types import SimpleNamespace

from agent.llm.schemas import LLMResponse, LLMToolCall
from core.runtime_engine.engine import SSOTRuntimeEngine
from core.runtime_engine.models import (
    ApprovedToolContinuation,
    SSOTRuntimeConfig,
    StatelessContext,
)
from core.runtime_engine.tool_runtime import ToolRuntime


def _storage(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_MASTER_KEY", "approval-test-master-key")


def test_continuation_claim_is_durable_and_single_owner(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.runtime.approval_continuation import (
        claim_ready_continuation,
        create_continuation,
        finish_continuation,
        record_decision,
    )

    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="执行检查",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "list"}}],
        approval_ids=["apr-1"],
    )
    ready = record_decision(
        workspace_id="default",
        continuation_id=continuation_id,
        approval_id="apr-1",
        allowed=True,
    )
    assert ready["status"] == "ready"
    first, grant, payload = claim_ready_continuation(
        workspace_id="default", continuation_id=continuation_id,
    )
    assert first["status"] == "claimed"
    assert isinstance(grant, ApprovedToolContinuation)
    assert payload["session_id"] == "session-1"

    second, duplicate_grant, duplicate_payload = claim_ready_continuation(
        workspace_id="default",
        continuation_id=continuation_id,
    )
    assert second["status"] == "claimed"
    assert duplicate_grant is None
    assert duplicate_payload is None
    assert finish_continuation("default", continuation_id, completed_run_id="run-2")["status"] == "completed"


def test_repeated_same_decision_is_idempotent(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.runtime.approval_continuation import create_continuation, record_decision

    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="执行检查",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "list"}}],
        approval_ids=["apr-1", "apr-2"],
    )
    first = record_decision(
        workspace_id="default", continuation_id=continuation_id,
        approval_id="apr-1", allowed=True,
    )
    second = record_decision(
        workspace_id="default", continuation_id=continuation_id,
        approval_id="apr-1", allowed=True,
    )
    assert first["status"] == "pending"
    assert second["decision_version"] == first["decision_version"]


def test_reconciler_visits_only_each_principals_allowed_workspaces(monkeypatch):
    import agent.runtime.continuation_reconciler as reconciler
    import backend.core.identity as identity
    import storage.principal as principal
    import storage.workspace_store as workspace_store

    monkeypatch.setattr(principal, "known_storage_principals", lambda: ["Admin", "network"])
    monkeypatch.setattr(principal, "principal_storage_key", lambda name: f"id-{name}")
    monkeypatch.setattr(identity, "get_user", lambda name: (
        {"workspace_ids": ["default"]} if name == "network" else None
    ))
    monkeypatch.setattr(workspace_store, "list_workspace_ids", lambda **_kwargs: ["default", "ops"])
    visited = []

    def fake_reconcile(workspace_id):
        visited.append((principal.current_storage_principal(), workspace_id))
        return {
            "pending": 1, "ready": 0, "claimed": 0, "dispatching": 0,
            "stalled": 0, "expired": 0, "decision_mismatch": 0,
            "oldest_pending_age_seconds": 5,
        }

    monkeypatch.setattr(reconciler, "reconcile_workspace", fake_reconcile)
    outcomes = reconciler.reconcile_all_workspaces()
    assert visited == [("Admin", "default"), ("Admin", "ops"), ("network", "default")]
    assert sorted(outcomes) == ["id-Admin:default", "id-Admin:ops", "id-network:default"]


def test_known_storage_principals_includes_configured_api_token(monkeypatch):
    monkeypatch.setenv("LZCORE_LOGIN_USERNAME", "Admin")
    monkeypatch.setenv("LZCORE_API_TOKEN_FILE", "/run/secrets/api_token")
    monkeypatch.setattr("backend.core.identity.list_users", lambda: [{"username": "network"}])
    from storage.principal import known_storage_principals
    assert known_storage_principals() == ["Admin", "api-token", "network"]


def test_continuation_encryption_accepts_mounted_master_key(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.delenv("LZCORE_MASTER_KEY", raising=False)
    master_key = tmp_path / "master-key"
    master_key.write_text("mounted-approval-master-key", encoding="utf-8")
    monkeypatch.setenv("LZCORE_MASTER_KEY_FILE", str(master_key))
    from agent.runtime.approval_continuation import create_continuation

    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="检查",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "list"}}],
        approval_ids=["apr-1"],
    )
    assert continuation_id.startswith("cont_")


def test_ordinary_approval_returns_pending_without_waiting(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    import agent.runtime.ssot_runtime as runtime

    created = []

    class Store:
        def create_batch(self, specs):
            created.extend(specs)
            return [SimpleNamespace(approval_id=item["approval_id"]) for item in specs]

        def wait(self, *_args, **_kwargs):
            raise AssertionError("ordinary approval must never block a runtime worker")

    monkeypatch.setattr(runtime, "get_approval_store", lambda _workspace_id: Store())
    from core.runtime_engine.cognitive_state import initialize_cognitive_state

    cognitive_state = initialize_cognitive_state(
        turn_id="run-1", trace_id="trace-1", user_input="删除文件",
    )
    cognitive_state.add_fact(
        "审批前已确认文件存在", source="workspace.file", evidence_id="read-1",
    )
    handler = runtime._build_approval_handler(
        workspace_id="default", session_id="session-1", run_id="run-1",
    )
    result = asyncio.run(handler(
        StatelessContext(
            workspace_id="default", session_id="session-1", request_id="run-1",
            user_input="删除文件", extras={"cognitive_state": cognitive_state},
        ),
        {
            "risk_level": "high",
            "approval_nodes": ["call-1"],
            "approval_details": [{"tool": "workspace.file", "risk_reason": "write"}],
            "tool_calls": [{"id": "call-1", "name": "workspace.file", "arguments": {"action": "delete"}}],
        },
    ))
    assert result["status"] == "pending"
    assert len(result["approval_ids"]) == 1
    assert result["approval_ids"][0].startswith("apr_")
    assert created[0]["metadata"]["continuation_id"] == result["continuation_id"]

    from agent.runtime.approval_continuation import (
        claim_ready_continuation, list_continuations, record_decision,
    )
    from storage.records import workspace_record_file

    record = list_continuations("default")[0]
    assert record["approval_count"] == 1
    assert "payload_ref" not in record
    raw_path = workspace_record_file(
        "default", "approvals", "continuations", f"{result['continuation_id']}.json"
    )
    raw = __import__("json").loads(raw_path.read_text(encoding="utf-8"))
    assert raw["approval_ids"] == result["approval_ids"]
    assert all(not item.startswith("pending_") for item in raw["approval_ids"])

    record_decision(
        workspace_id="default", continuation_id=result["continuation_id"],
        approval_id=result["approval_ids"][0], allowed=True,
    )
    _record, _grant, payload = claim_ready_continuation(
        workspace_id="default", continuation_id=result["continuation_id"],
    )
    assert payload["cognitive_state"]["known_facts"][0]["fact"] == "审批前已确认文件存在"
def test_approval_batch_failure_compensates_continuation(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    import agent.runtime.ssot_runtime as runtime

    class Store:
        def create_batch(self, _specs):
            raise OSError("disk full")

    monkeypatch.setattr(runtime, "get_approval_store", lambda _workspace_id: Store())
    handler = runtime._build_approval_handler(
        workspace_id="default", session_id="session-1", run_id="run-1",
    )
    try:
        asyncio.run(handler(
            StatelessContext(
                workspace_id="default", session_id="session-1", request_id="run-1",
                user_input="删除文件",
            ),
            {
                "risk_level": "high",
                "approval_nodes": ["call-1"],
                "approval_details": [{"tool": "workspace.file", "risk_reason": "write"}],
                "tool_calls": [{"id": "call-1", "name": "workspace.file", "arguments": {"action": "delete"}}],
            },
        ))
    except OSError:
        pass
    else:
        raise AssertionError("persistence failure must fail the approval turn")

    from agent.runtime.approval_continuation import list_continuations
    assert list_continuations("default") == []


def test_stale_running_continuation_is_observable_but_never_replayed(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    monkeypatch.setenv("LZCORE_CONTINUATION_STALL_SECONDS", "60")
    from agent.runtime.approval_continuation import (
        claim_ready_continuation,
        close_stalled_continuation,
        create_continuation,
        list_continuations,
        maintain_continuations,
        record_decision,
    )
    from storage.atomic_io import atomic_write_json
    from storage.records import workspace_record_file

    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="执行检查",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "list"}}],
        approval_ids=["apr-1"],
    )
    record_decision(
        workspace_id="default",
        continuation_id=continuation_id,
        approval_id="apr-1",
        allowed=True,
    )
    _, grant, _ = claim_ready_continuation(
        workspace_id="default", continuation_id=continuation_id,
    )
    assert grant is not None
    path = workspace_record_file(
        "default", "approvals", "continuations", f"{continuation_id}.json"
    )
    record = __import__("json").loads(path.read_text(encoding="utf-8"))
    record["heartbeat_at"] = "2000-01-01T00:00:00+00:00"
    atomic_write_json(path, record)

    assert maintain_continuations("default", force=True)["stalled"] == 1
    public = list_continuations("default", status="stalled")
    assert public[0]["continuation_id"] == continuation_id
    assert public[0]["stall_reason"] == "execution_heartbeat_expired"
    _, duplicate_grant, _ = claim_ready_continuation(
        workspace_id="default",
        continuation_id=continuation_id,
    )
    assert duplicate_grant is None
    closed = close_stalled_continuation(
        "default", continuation_id, reason="已核对设备状态，操作未完成"
    )
    assert closed["status"] == "failed"
    assert closed["execution_phase"] == "operator_closed"


def test_orphan_pending_continuation_expires_and_releases_secret(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    monkeypatch.setenv("LZCORE_APPROVAL_TTL_SECONDS", "60")
    from agent.runtime.approval_continuation import (
        create_continuation,
        maintain_continuations,
    )
    from storage.atomic_io import atomic_write_json
    from storage.records import workspace_record_file
    from storage.secret_store import get_secret

    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="删除",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "delete"}}],
        approval_ids=["apr-orphan"],
    )
    path = workspace_record_file(
        "default", "approvals", "continuations", f"{continuation_id}.json"
    )
    record = __import__("json").loads(path.read_text(encoding="utf-8"))
    secret_ref = record["payload_ref"]
    record["created_at"] = "2000-01-01T00:00:00+00:00"
    record["updated_at"] = record["created_at"]
    atomic_write_json(path, record)

    assert maintain_continuations("default", force=True)["expired"] == 1
    expired = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert expired["status"] == "expired"
    assert get_secret(secret_ref) == ""


def test_typed_continuation_reenters_canonical_query_loop():
    calls = []
    config = SSOTRuntimeConfig(max_query_loop_iterations=2)
    runtime = ToolRuntime(config)
    runtime.register("workspace.file", lambda arguments: calls.append(dict(arguments)) or {"ok": True, "files": []})
    registry = {
        "workspace.file": {
            "description": "files",
            "args_schema": {
                "type": "object",
                "required": ["action"],
                "properties": {"action": {"type": "string", "enum": ["list"]}},
            },
        },
    }
    engine = SSOTRuntimeEngine(
        config=config,
        llm_invoke=lambda **_kwargs: LLMResponse(content="已继续并完成。"),
        tool_registry=registry,
        tool_runtime=runtime,
    )
    result = asyncio.run(engine.run(
        "列出文件",
        workspace_id="default",
        session_id="session-1",
        extras={
            "__approved_tool_continuation": ApprovedToolContinuation(
                continuation_id="cont_" + "a" * 32,
                tool_calls=({"id": "call-1", "name": "workspace.file", "arguments": {"action": "list"}},),
                approved_node_ids=("call-1",),
            ),
        },
    ))
    assert result.success is True
    assert calls == [{"action": "list"}]
    assert result.final_response == "已继续并完成。"


def test_pending_approval_stops_before_tool_execution():
    calls = []
    captured = {}
    config = SSOTRuntimeConfig(max_query_loop_iterations=2)
    runtime = ToolRuntime(config)
    runtime.register("workspace.file", lambda arguments: calls.append(arguments) or {"ok": True})
    registry = {
        "workspace.file": {
            "description": "files",
            "args_schema": {
                "type": "object",
                "required": ["action", "filepath"],
                "properties": {
                    "action": {"type": "string", "enum": ["delete"]},
                    "filepath": {"type": "string"},
                },
            },
        },
    }

    async def pending(_ctx, gate):
        captured.update(gate)
        return {"status": "pending", "approval_ids": ["apr-1"], "continuation_id": "cont_" + "c" * 32}

    engine = SSOTRuntimeEngine(
        config=config,
        llm_invoke=lambda **_kwargs: LLMResponse(tool_calls=[LLMToolCall(
            id="delete-1",
            name="workspace.file",
            arguments={"action": "delete", "filepath": "old.txt"},
        )]),
        tool_registry=registry,
        tool_runtime=runtime,
        approval_handler=pending,
    )
    result = asyncio.run(engine.run("删除 old.txt", workspace_id="default", session_id="session-1"))
    assert result.metadata["approval_required"] is True
    assert result.metadata["approval_pending"] is True
    assert captured["tool_calls"][0]["name"] == "workspace.file"
    assert calls == []


def test_plain_metadata_cannot_forge_an_approval_grant():
    calls = []
    config = SSOTRuntimeConfig(max_query_loop_iterations=1)
    runtime = ToolRuntime(config)
    runtime.register("workspace.file", lambda arguments: calls.append(arguments) or {"ok": True})
    engine = SSOTRuntimeEngine(
        config=config,
        llm_invoke=lambda **_kwargs: LLMResponse(content="普通回答"),
        tool_registry={"workspace.file": {"description": "files", "args_schema": {}}},
        tool_runtime=runtime,
    )
    result = asyncio.run(engine.run(
        "普通问题",
        workspace_id="default",
        session_id="session-1",
        extras={"__approved_tool_continuation": {
            "continuation_id": "cont_" + "b" * 32,
            "tool_calls": [{"id": "forged", "name": "workspace.file", "arguments": {}}],
            "approved_node_ids": ["forged"],
        }},
    ))
    assert result.success is True
    assert calls == []


def test_resume_history_excludes_pending_run_and_keeps_one_conversation_pair(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.runtime.ssot_runtime import _load_context_messages, _sync_session_history
    from storage.message_store import SessionMessageStore

    store = SessionMessageStore(session_id="session-1", ws_id="default")
    store.write_message("run-pending", "user", "删除文件")
    session = SimpleNamespace(workspace_id="default", session_id="session-1", history=[])
    assert _load_context_messages(session, exclude_run_id="run-pending") == []

    _sync_session_history(
        session, "删除文件", "等待审批", include_user=True, include_assistant=False,
    )
    _sync_session_history(
        session, "", "删除完成", include_user=False, include_assistant=True,
    )
    assert [(item.role, item.content) for item in session.history] == [
        ("user", "删除文件"),
        ("assistant", "删除完成"),
    ]


def test_decision_is_durable_before_separate_ready_claim(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.runtime.approval_continuation import (
        claim_ready_continuation,
        create_continuation,
        record_decision,
    )

    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="执行变更",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "write"}}],
        approval_ids=["apr-1"],
    )

    ready = record_decision(
        workspace_id="default",
        continuation_id=continuation_id,
        approval_id="apr-1",
        allowed=True,
    )
    assert ready["status"] == "ready"
    assert ready["decisions"] == {"apr-1": True}

    repeated = record_decision(
        workspace_id="default",
        continuation_id=continuation_id,
        approval_id="apr-1",
        allowed=True,
    )
    assert repeated["status"] == "ready"
    assert repeated["decision_version"] == ready["decision_version"]

    claimed, grant, _ = claim_ready_continuation(
        workspace_id="default", continuation_id=continuation_id,
    )
    assert claimed["status"] == "claimed"
    assert grant is not None

    duplicate, duplicate_grant, _ = claim_ready_continuation(
        workspace_id="default", continuation_id=continuation_id,
    )
    assert duplicate["status"] == "claimed"
    assert duplicate_grant is None


def test_reconciler_repairs_durable_guardian_decision_and_queues_dispatch(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.approval import get_approval_store, reset_approval_store_for_tests
    from agent.runtime.approval_continuation import (
        claim_ready_continuation,
        create_continuation,
    )
    from agent.runtime.continuation_reconciler import reconcile_workspace
    import agent.runtime.continuation_dispatcher as dispatcher

    queued = []
    monkeypatch.setattr(
        dispatcher,
        "dispatch_ready_continuation",
        lambda workspace_id, current_id: queued.append((workspace_id, current_id)) or True,
    )
    reset_approval_store_for_tests(remove_persisted=True)
    approval_id = "apr_000000000001"
    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="执行变更",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "write"}}],
        approval_ids=[approval_id],
    )
    store = get_approval_store("default")
    store.create_batch([{
        "approval_id": approval_id,
        "session_id": "session-1",
        "tool_id": "workspace.file",
        "arguments": {"action": "write"},
        "description": "write",
        "risk_level": "high",
        "workspace_id": "default",
        "run_id": "run-1",
        "metadata": {"continuation_id": continuation_id},
    }])
    assert store.resolve(approval_id, True, workspace_id="default") is not None

    result = reconcile_workspace("default")
    assert result["decision_repaired"] == 1
    assert result["ready"] == 1
    assert result["dispatch_queued"] == 1
    assert queued == [("default", continuation_id)]

    claimed, grant, _ = claim_ready_continuation(
        workspace_id="default", continuation_id=continuation_id,
    )
    assert claimed["status"] == "claimed"
    assert grant is not None


def test_continuation_payload_keeps_server_cognitive_snapshot(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from types import SimpleNamespace
    from agent.runtime.approval_continuation import claim_ready_continuation, create_continuation, record_decision
    from core.runtime_engine.cognitive_state import initialize_cognitive_state

    state = initialize_cognitive_state(turn_id="parent-turn", trace_id="parent-trace", user_input="核对并执行变更")
    state.register_tool_results([SimpleNamespace(
        tool_name="probe", call_id="parent-read", ok=True,
        output={"summary": "父运行已确认配置为 A"}, summary="父运行已确认配置为 A",
    )])
    continuation_id = create_continuation(
        workspace_id="default", session_id="session-1", parent_run_id="parent-run",
        user_input="核对并执行变更",
        tool_calls=[{"id": "approved-write", "name": "workspace.file", "arguments": {"action": "write", "filename": "approved.txt", "content": "ok"}}],
        approval_ids=["approval-1"], cognitive_state=state.as_trace_payload(),
    )
    record_decision(workspace_id="default", continuation_id=continuation_id, approval_id="approval-1", allowed=True)
    _record, _grant, payload = claim_ready_continuation(workspace_id="default", continuation_id=continuation_id)
    assert payload["cognitive_state"]["known_facts"][0]["fact"] == "父运行已确认配置为 A"
    assert payload["cognitive_state"]["events"][-1]["type"] == "cognitive_evidence_registered"


def test_approved_resume_inherits_cognitive_facts_without_reemitting_parent_events():
    import asyncio
    from types import SimpleNamespace
    from agent.llm.schemas import LLMResponse
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.models import ApprovedToolContinuation, SSOTRuntimeConfig
    from core.runtime_engine.cognitive_state import initialize_cognitive_state
    from core.runtime_engine.tool_runtime import ToolRuntime

    parent = initialize_cognitive_state(turn_id="parent-turn", trace_id="parent-trace", user_input="先核对再执行")
    parent.register_tool_results([SimpleNamespace(
        tool_name="data.manage", call_id="parent-read", ok=True,
        output={"fact_key": "device.config", "summary": "已确认配置版本为 A"},
        summary="已确认配置版本为 A",
    )])
    parent_event_ids = {event["event_id"] for event in parent.events}
    captured_messages = []

    class CaptureEmitter:
        def __init__(self):
            self.calls = []
        def emit(self, name, payload):
            self.calls.append((name, payload))

    emitter = CaptureEmitter()
    config = SSOTRuntimeConfig(max_query_loop_iterations=2)
    runtime = ToolRuntime(config)
    runtime.register("workspace.file", lambda _arguments: {
        "ok": True,
        "fact_key": "device.change",
        "summary": "已执行批准的配置变更",
    })

    def invoke(**kwargs):
        captured_messages.append(list(kwargs["messages"]))
        return LLMResponse(content="批准后的观察已足够。")

    engine = SSOTRuntimeEngine(
        config=config,
        llm_invoke=invoke,
        tool_registry={
            "workspace.file": {
                "description": "write file",
                "args_schema": {
                    "type": "object",
                    "required": ["action"],
                    "properties": {"action": {"type": "string"}},
                },
            },
        },
        tool_runtime=runtime,
        emitter=emitter,
    )
    result = asyncio.run(engine.run(
        "先核对再执行", workspace_id="default", session_id="approval-resume",
        extras={
            "__approved_tool_continuation": ApprovedToolContinuation(
                continuation_id="cont_" + "a" * 32,
                tool_calls=({"id": "approved-write", "name": "workspace.file", "arguments": {"action": "write", "filename": "approved.txt", "content": "ok"}},),
                approved_node_ids=("approved-write",),
            ),
            "__approval_continuation_resume": True,
            "__approval_cognitive_state": parent.as_trace_payload(),
        },
    ))

    assert result.success is True, result.errors
    assert result.metadata["cognitive"]["known_fact_count"] == 2
    first_llm_cognitive = next(
        str(message.content)
        for message in captured_messages[0]
        if 'source_kind="cognitive_state"' in str(message.content)
    )
    assert '"known_fact_count":2' in first_llm_cognitive
    emitted_events = [payload for name, payload in emitter.calls if name.startswith("cognitive_")]
    assert all(event["event_id"] not in parent_event_ids for event in emitted_events)
    assert sum(event["type"] == "cognitive_evidence_registered" for event in emitted_events) == 1


def test_batch_approval_binds_each_node_to_its_exact_canonical_call(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    import agent.runtime.ssot_runtime as runtime

    created = []

    class Store:
        def create_batch(self, specs):
            created.extend(specs)
            return [SimpleNamespace(approval_id=item["approval_id"]) for item in specs]

    monkeypatch.setattr(runtime, "get_approval_store", lambda _workspace_id: Store())
    handler = runtime._build_approval_handler(
        workspace_id="default", session_id="session-1", run_id="run-1",
    )
    result = asyncio.run(handler(
        StatelessContext(
            workspace_id="default", session_id="session-1", request_id="run-1",
            user_input="删除 old.txt 并清理临时目录",
        ),
        {
            "risk_level": "high",
            "approval_nodes": ["delete-1", "exec-1"],
            # Deliberately reversed: binding must use node_id, never zip order.
            "approval_details": [
                {"node_id": "exec-1", "tool": "exec.run", "risk_reason": "rm -rf"},
                {"node_id": "delete-1", "tool": "workspace.file", "risk_reason": "delete"},
            ],
            "tool_calls": [
                {"id": "delete-1", "name": "workspace.file", "arguments": {"action": "delete", "filepath": "old.txt"}},
                {"id": "exec-1", "name": "exec.run", "arguments": {"command": "rm -rf /tmp/lzcore-probe"}},
            ],
        },
    ))

    assert len(result["approval_ids"]) == 2
    assert len(created) == 2
    assert [spec["tool_id"] for spec in created] == ["workspace.file", "exec.run"]
    assert [spec["arguments"] for spec in created] == [
        {"action": "delete", "filepath": "old.txt"},
        {"command": "rm -rf /tmp/lzcore-probe", "target": "local", "shell": "cmd", "action": "shell"},
    ]
    assert [spec["metadata"]["node_id"] for spec in created] == ["delete-1", "exec-1"]


def test_batch_approval_without_risk_details_still_issues_one_exact_grant_per_node(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    import agent.runtime.ssot_runtime as runtime

    created = []

    class Store:
        def create_batch(self, specs):
            created.extend(specs)
            return [SimpleNamespace(approval_id=item["approval_id"]) for item in specs]

    monkeypatch.setattr(runtime, "get_approval_store", lambda _workspace_id: Store())
    handler = runtime._build_approval_handler(
        workspace_id="default", session_id="session-1", run_id="run-1",
    )
    result = asyncio.run(handler(
        StatelessContext(
            workspace_id="default", session_id="session-1", request_id="run-1",
            user_input="删除两个文件",
        ),
        {
            "risk_level": "high",
            "approval_nodes": ["delete-a", "delete-b"],
            "approval_details": [],
            "tool_calls": [
                {"id": "delete-a", "name": "workspace.file", "arguments": {"action": "delete", "filepath": "a.txt"}},
                {"id": "delete-b", "name": "workspace.file", "arguments": {"action": "delete", "filepath": "b.txt"}},
            ],
        },
    ))

    assert len(result["approval_ids"]) == 2
    assert [spec["arguments"] for spec in created] == [
        {"action": "delete", "filepath": "a.txt"},
        {"action": "delete", "filepath": "b.txt"},
    ]


def test_reconciler_queues_durably_ready_continuation_after_crash_window(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.approval import get_approval_store, reset_approval_store_for_tests
    from agent.runtime.approval_continuation import create_continuation, list_continuations
    from agent.runtime.continuation_reconciler import reconcile_workspace
    import agent.runtime.continuation_dispatcher as dispatcher

    queued = []
    monkeypatch.setattr(
        dispatcher,
        "dispatch_ready_continuation",
        lambda workspace_id, continuation_id: queued.append((workspace_id, continuation_id)) or True,
    )
    reset_approval_store_for_tests(remove_persisted=True)
    approval_id = "apr_000000000009"
    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="执行已批准变更",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "write", "filename": "queued.txt", "content": "ok"}}],
        approval_ids=[approval_id],
    )
    store = get_approval_store("default")
    store.create_batch([{
        "approval_id": approval_id,
        "session_id": "session-1",
        "tool_id": "workspace.file",
        "arguments": {"action": "write", "filename": "queued.txt", "content": "ok"},
        "description": "write",
        "risk_level": "high",
        "workspace_id": "default",
        "run_id": "run-1",
        "metadata": {"continuation_id": continuation_id, "node_id": "call-1"},
    }])
    assert store.resolve(approval_id, True, workspace_id="default") is not None

    result = reconcile_workspace("default")

    assert result["decision_repaired"] == 1
    assert result["dispatch_queued"] == 1
    assert queued == [("default", continuation_id)]
    assert list_continuations("default", status="ready")[0]["continuation_id"] == continuation_id


def test_dispatch_worker_claims_once_before_any_resume(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.runtime.approval_continuation import (
        create_continuation,
        list_continuations,
        record_decision,
    )
    import agent.runtime.continuation_dispatcher as dispatcher

    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="执行已批准变更",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "write", "filename": "once.txt", "content": "ok"}}],
        approval_ids=["apr_dispatch_once"],
    )
    record_decision(
        workspace_id="default",
        continuation_id=continuation_id,
        approval_id="apr_dispatch_once",
        allowed=True,
    )
    resumed = []
    monkeypatch.setattr(
        dispatcher,
        "_resume_claimed_continuation",
        lambda workspace_id, current_id, grant, payload: resumed.append((workspace_id, current_id, grant, payload)),
    )

    dispatcher._claim_and_resume("default", continuation_id)
    dispatcher._claim_and_resume("default", continuation_id)

    assert len(resumed) == 1
    assert resumed[0][0:2] == ("default", continuation_id)
    assert list_continuations("default", status="claimed")[0]["continuation_id"] == continuation_id


def test_approved_continuation_projects_terminal_result_to_parent_turn(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from types import SimpleNamespace
    from agent.runtime.turn_persistence import project_approved_continuation_result
    from storage.message_store import SessionMessageStore
    from storage.run_record_store import get_run
    from storage.records import atomic_save_json

    parent_run_id = "parent-run-1"
    atomic_save_json("default", ("runs", f"{parent_run_id}.json"), {
        "run_id": parent_run_id,
        "session_id": "session-1",
        "status": "pending",
        "ok": True,
        "execution_outcome": "partial",
        "final_response": "该操作正在等待审批，批准后将从当前步骤继续。",
    })
    store = SessionMessageStore(session_id="session-1", ws_id="default")
    store.write_message(parent_run_id, "user", "删除临时文件", metadata={"created_at": "2026-08-17T00:00:00+00:00"})
    resumed = SimpleNamespace(
        ok=True, turn_id="continuation-run-1", final_response="删除已落地。",
        errors=[], warnings=[], tool_calls=[{"name": "workspace.file", "ok": True}],
        trace_id="trace-1",
        metadata={"execution_outcome": "complete", "tool_execution_outcome": "complete"},
    )
    assert project_approved_continuation_result(
        workspace_id="default", session_id="session-1", parent_run_id=parent_run_id,
        continuation_id="cont_" + "a" * 32, resumed=resumed,
    )["status"] == "ok"
    parent = get_run(parent_run_id, "default")
    assert parent["status"] == "ok"
    assert parent["execution_outcome"] == "complete"
    assert parent["final_response"] == "删除已落地。"
    assert parent["metadata"]["approval_continuation"]["completed_run_id"] == "continuation-run-1"
    assert [(m["run_id"], m["role"], m["content"]) for m in store.get_messages()] == [
        (parent_run_id, "user", "删除临时文件"),
        (parent_run_id, "assistant", "删除已落地。"),
    ]


def test_approval_required_parent_run_projects_pending_not_partial(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.runtime.turn_persistence import project_approval_pending_parent
    from storage.run_record_store import get_run
    from storage.records import atomic_save_json

    parent_run_id = "parent-run-2"
    atomic_save_json("default", ("runs", f"{parent_run_id}.json"), {
        "run_id": parent_run_id, "session_id": "session-1", "status": "partial",
        "ok": True, "execution_outcome": "partial",
    })
    projected = project_approval_pending_parent(
        workspace_id="default", parent_run_id=parent_run_id,
        continuation_id="cont_" + "b" * 32,
    )
    assert projected["status"] == "pending"
    assert get_run(parent_run_id, "default")["status"] == "pending"


def test_unsuccessful_resume_without_errors_has_fail_closed_reason():
    from agent.runtime.continuation_dispatcher import _resume_failure_reason

    assert _resume_failure_reason(SimpleNamespace(ok=True, errors=[])) == ""
    assert _resume_failure_reason(SimpleNamespace(ok=False, errors=[])) == "approval_resume_unsuccessful"
    assert _resume_failure_reason(SimpleNamespace(ok=False, errors=["tool_failed"])) == "tool_failed"


def test_approval_decision_projects_running_and_rejection_into_parent(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.runtime.approval_continuation import create_continuation, record_decision
    from agent.runtime.turn_persistence import project_approval_pending_parent
    from storage.records import atomic_save_json
    from storage.run_record_store import get_run
    from storage.message_store import SessionMessageStore

    for allowed, expired, expected in [(True, False, "ready"), (False, False, "rejected"), (False, True, "expired")]:
        run_id = f"parent-{expected}"
        atomic_save_json("default", ("runs", f"{run_id}.json"), {"run_id": run_id, "session_id": "session-1", "status": "pending", "ok": True})
        cid = create_continuation(workspace_id="default", session_id="session-1", parent_run_id=run_id, user_input="test", tool_calls=[{"id": "a", "name": "test.tool", "arguments": {}}], approval_ids=["apr-a"])
        project_approval_pending_parent(workspace_id="default", parent_run_id=run_id, continuation_id=cid)
        record_decision(workspace_id="default", continuation_id=cid, approval_id="apr-a", allowed=allowed, expired=expired)
        parent = get_run(run_id, "default")
        assert parent["metadata"]["approval_continuation"]["status"] == expected
        assert parent["metadata"]["approval_required"] is False
        if allowed:
            assert parent["status"] == "running"
        else:
            assert parent["status"] == "error"
            messages = SessionMessageStore(session_id="session-1", ws_id="default").get_messages()
            assert any(item["run_id"] == run_id and "待审批操作未执行" in item["content"] for item in messages)


def test_continuation_projection_rejects_parent_run_from_other_session(monkeypatch, tmp_path):
    _storage(monkeypatch, tmp_path)
    from agent.runtime.turn_persistence import project_approved_continuation_result
    from storage.records import atomic_save_json
    from storage.run_record_store import get_run

    parent_run_id = "other-session-parent"
    atomic_save_json("default", ("runs", f"{parent_run_id}.json"), {
        "run_id": parent_run_id, "session_id": "session-other",
        "status": "pending", "final_response": "等待审批",
    })
    projected = project_approved_continuation_result(
        workspace_id="default", session_id="session-current", parent_run_id=parent_run_id,
        continuation_id="cont_" + "d" * 32,
        resumed=SimpleNamespace(
            ok=True, turn_id="continuation-run", final_response="不应写入其他会话",
            errors=[], warnings=[], tool_calls=[], trace_id="trace", metadata={},
        ),
    )
    assert projected == {}
    parent = get_run(parent_run_id, "default")
    assert parent["status"] == "pending"
    assert parent["final_response"] == "等待审批"
