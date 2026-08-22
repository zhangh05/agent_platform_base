from pathlib import Path
from types import SimpleNamespace
import asyncio


def _ctx():
    return SimpleNamespace(
        workspace_id="default",
        request_id="turn-1",
        session_id="session-1",
        extras={"risk_level": "high"},
    )


def test_operation_ledger_persists_unknown_without_arguments(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import (
        finish_operation,
        plan_operation,
        start_operation,
    )

    operation = plan_operation(_ctx(), "workspace.file", "call-1", {"token": "secret", "action": "write"})
    assert "token" not in str(operation)
    start_operation("default", operation["operation_id"])
    result = SimpleNamespace(
        ok=False,
        error="remote action may continue",
        error_code="TOOL_TIMEOUT_UNCERTAIN",
        execution_may_continue=True,
        output={"executed": True},
    )
    finished = finish_operation("default", operation["operation_id"], result)
    assert finished["status"] == "unknown"
    path = Path(str(tmp_path / "workspaces")) / "default" / "operations" / f'{operation["operation_id"]}.json'
    assert path.is_file()
    assert "secret" not in path.read_text()


def test_operation_ledger_marks_unstarted_operation_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import (
        finish_operation,
        plan_operation,
        start_operation,
    )

    operation = plan_operation(_ctx(), "workspace.file", "call-2", {"action": "write"})
    start_operation("default", operation["operation_id"])
    result = SimpleNamespace(
        ok=False,
        error="budget exhausted before start",
        error_code="TOOL_BUDGET_EXHAUSTED",
        execution_may_continue=False,
        output={"executed": False},
    )
    assert finish_operation("default", operation["operation_id"], result)["status"] == "blocked"


def test_request_budget_detached_completion_settles_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import finish_operation, list_operations, plan_operation, start_operation
    from core.runtime_engine.query_loop import StreamingToolExecutor, StreamingToolResult

    operation = plan_operation(_ctx(), "agent.manage", "call-budget", {"action": "spawn"})
    start_operation("default", operation["operation_id"])
    finish_operation("default", operation["operation_id"], SimpleNamespace(
        ok=False,
        error="request budget expired",
        error_code="TOOL_BUDGET_TIMEOUT_UNCERTAIN",
        execution_may_continue=True,
        output={"executed": True},
    ))

    async def complete():
        return StreamingToolResult(
            tool_name="agent.manage",
            call_id="call-budget",
            output={"ok": True, "subtask_id": "sub-late", "summary": "done"},
            ok=True,
        )

    async def exercise():
        task = asyncio.create_task(complete())
        await task
        StreamingToolExecutor._settle_budget_detached_operation(task, "default", operation["operation_id"])

    asyncio.run(exercise())
    record = list_operations("default")[0]
    assert record["status"] == "succeeded"
    assert record["resolved_by"] == "request_budget_handler"
    assert record["resource_kind"] == "subagent"
    assert record["resource_id"] == "sub-late"


def test_job_creation_inherits_runtime_operation_link(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import list_operations, plan_operation, start_operation
    from core.tools.context import bind_runtime_operation_context, reset_runtime_operation_context
    from jobs.manager import create_job

    operation = plan_operation(_ctx(), "workflow.manage", "call-job", {"action": "run"})
    start_operation("default", operation["operation_id"])
    token = bind_runtime_operation_context("default", operation["operation_id"], "call-job")
    try:
        job = create_job(
            workspace_id="default",
            job_type="workflow_run",
            title="linked job",
            payload={"workflow_id": "network_asset_read", "inputs": {}},
            enqueue=False,
        )
    finally:
        reset_runtime_operation_context(token)

    record = list_operations("default")[0]
    assert record["resource_kind"] == "job"
    assert record["resource_id"] == job.job_id


def test_operation_ledger_public_projection_redacts_result_details(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import finish_operation, list_operations, plan_operation, start_operation

    operation = plan_operation(_ctx(), "workspace.file", "call-public", {"token": "secret"})
    start_operation("default", operation["operation_id"])
    finish_operation("default", operation["operation_id"], SimpleNamespace(
        ok=False,
        error="token=secret remote write uncertain",
        error_code="TOOL_TIMEOUT_UNCERTAIN",
        execution_may_continue=True,
        output={"executed": True, "summary": "token=secret summary"},
    ))
    records = list_operations("default")

    assert len(records) == 1
    assert records[0]["status"] == "unknown"
    assert records[0]["operation_id"] == operation["operation_id"]
    assert "arguments_sha256" not in records[0]
    assert "secret" not in str(records[0])
    assert "[REDACTED_SECRET]" in records[0]["error"]


def test_linked_durable_resource_reconciles_unknown_operation(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import (
        finish_operation,
        link_operation_resource,
        list_operations,
        plan_operation,
        reconcile_operations,
        start_operation,
    )
    operation = plan_operation(_ctx(), "agent.manage", "call-linked", {"action": "spawn"})
    start_operation("default", operation["operation_id"])
    link_operation_resource("default", operation["operation_id"], resource_kind="subagent", resource_id="sub-12345678")
    finish_operation("default", operation["operation_id"], SimpleNamespace(
        ok=False,
        error="timed out",
        error_code="TOOL_TIMEOUT_UNCERTAIN",
        execution_may_continue=True,
        output={"executed": True},
    ))
    monkeypatch.setattr(
        "agent.runtime.durable.subagent.get_subagent_task",
        lambda _workspace_id, _subtask_id: {"status": "succeeded", "summary": "done"},
    )
    assert reconcile_operations("default")["resolved"] == 1
    record = list_operations("default")[0]
    assert record["status"] == "succeeded"
    assert record["resolved_by"] == "durable_resource"
    assert record["resource_id"] == "sub-12345678"


def test_manual_resolution_requires_reason_and_preserves_audit(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import (
        finish_operation,
        plan_operation,
        resolve_operation_manually,
        start_operation,
    )
    operation = plan_operation(_ctx(), "workspace.file", "call-manual", {"action": "write"})
    start_operation("default", operation["operation_id"])
    finish_operation("default", operation["operation_id"], SimpleNamespace(
        ok=False, error="uncertain", error_code="TOOL_TIMEOUT_UNCERTAIN",
        execution_may_continue=True, output={"executed": True},
    ))
    try:
        resolve_operation_manually("default", operation["operation_id"], status="succeeded", reason="")
    except ValueError as exc:
        assert str(exc) == "resolution_reason_required"
    else:
        raise AssertionError("manual resolution must require evidence")
    resolved = resolve_operation_manually(
        "default", operation["operation_id"], status="succeeded", reason="已核对目标系统记录",
    )
    assert resolved["status"] == "succeeded"
    assert resolved["resolved_by"] == "manual"
    assert resolved["resolution_reason"] == "已核对目标系统记录"


def test_detached_handler_eventually_settles_timeout_truth(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.models import ExecutionNode, SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.operation_ledger import finish_operation, list_operations, plan_operation, start_operation
    from core.runtime_engine.query_loop import StreamingToolExecutor
    from core.runtime_engine.tool_runtime import ToolRuntime
    from core.tools.context import bind_runtime_operation_context, reset_runtime_operation_context

    async def scenario():
        operation = plan_operation(_ctx(), "agent.manage", "call-detached", {"action": "spawn"})
        start_operation("default", operation["operation_id"])
        runtime = ToolRuntime(SSOTRuntimeConfig(single_node_timeout_ms=10))

        async def delayed(_args):
            await asyncio.sleep(0.05)
            return {"ok": True, "summary": "durable task completed", "subtask_id": "sub-87654321"}

        runtime.register("agent.manage", delayed)
        context = StatelessContext(
            workspace_id="default", session_id="session-1", request_id="turn-1", user_input="spawn",
        )
        token = bind_runtime_operation_context("default", operation["operation_id"], "call-detached")
        try:
            result = await runtime.execute_node(
                ExecutionNode(id="call-detached", tool="agent.manage", args={}), context, {},
            )
        finally:
            reset_runtime_operation_context(token)
        streaming = StreamingToolExecutor._from_tool_result(result, fallback_call_id="call-detached")
        finish_operation("default", operation["operation_id"], streaming)
        assert list_operations("default")[0]["status"] == "unknown"
        await asyncio.sleep(0.08)
        return list_operations("default")[0]

    settled = asyncio.run(scenario())
    assert settled["status"] == "succeeded"
    assert settled["resolved_by"] == "timed_out_handler"
    assert settled["resource_id"] == "sub-87654321"


def test_operation_resolution_admin_route_requires_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_LOGIN_ENABLED", "false")
    from core.runtime_engine.operation_ledger import finish_operation, plan_operation, start_operation
    operation = plan_operation(_ctx(), "workspace.file", "call-api", {"action": "write"})
    start_operation("default", operation["operation_id"])
    finish_operation("default", operation["operation_id"], SimpleNamespace(
        ok=False, error="uncertain", error_code="TOOL_TIMEOUT_UNCERTAIN",
        execution_may_continue=True, output={"executed": True},
    ))
    from backend.main import create_app
    client = create_app().test_client()
    url = f'/api/admin/operation-ledger/{operation["operation_id"]}/resolve'
    assert client.post(url, json={"workspace_id": "default", "status": "succeeded", "reason": "checked"}).status_code == 400
    response = client.post(url, json={
        "workspace_id": "default",
        "status": "succeeded",
        "reason": "已核对目标系统记录",
        "confirmation": f'RESOLVE {operation["operation_id"]}',
    })
    assert response.status_code == 200
    assert response.get_json()["status"] == "succeeded"
