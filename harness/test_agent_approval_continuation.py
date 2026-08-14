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
        create_continuation,
        finish_continuation,
        record_decision_and_claim,
    )

    continuation_id = create_continuation(
        workspace_id="default",
        session_id="session-1",
        parent_run_id="run-1",
        user_input="执行检查",
        tool_calls=[{"id": "call-1", "name": "workspace.file", "arguments": {"action": "list"}}],
        approval_ids=["apr-1"],
    )
    first, grant, payload = record_decision_and_claim(
        workspace_id="default",
        continuation_id=continuation_id,
        approval_id="apr-1",
        allowed=True,
    )
    assert first["status"] == "running"
    assert isinstance(grant, ApprovedToolContinuation)
    assert payload["session_id"] == "session-1"

    second, duplicate_grant, duplicate_payload = record_decision_and_claim(
        workspace_id="default",
        continuation_id=continuation_id,
        approval_id="apr-1",
        allowed=True,
    )
    assert second["status"] == "running"
    assert duplicate_grant is None
    assert duplicate_payload is None
    assert finish_continuation("default", continuation_id, completed_run_id="run-2")["status"] == "completed"


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
    handler = runtime._build_approval_handler(
        workspace_id="default", session_id="session-1", run_id="run-1",
    )
    result = asyncio.run(handler(
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
    assert result["status"] == "pending"
    assert len(result["approval_ids"]) == 1
    assert result["approval_ids"][0].startswith("apr_")
    assert created[0]["metadata"]["continuation_id"] == result["continuation_id"]

    from agent.runtime.approval_continuation import list_continuations
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
        close_stalled_continuation,
        create_continuation,
        list_continuations,
        maintain_continuations,
        record_decision_and_claim,
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
    _, grant, _ = record_decision_and_claim(
        workspace_id="default",
        continuation_id=continuation_id,
        approval_id="apr-1",
        allowed=True,
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
    _, duplicate_grant, _ = record_decision_and_claim(
        workspace_id="default",
        continuation_id=continuation_id,
        approval_id="apr-1",
        allowed=True,
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
