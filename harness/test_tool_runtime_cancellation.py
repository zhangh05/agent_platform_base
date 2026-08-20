from __future__ import annotations

import asyncio
import threading
import time


def test_shell_execution_cooperatively_cancels_process_tree(tmp_path):
    from core.tools.general_tools.shared import _run_shell

    cancelled = threading.Event()
    setter = threading.Timer(0.15, cancelled.set)
    setter.start()
    try:
        result = _run_shell(
            "python3 -c \"import time; time.sleep(10)\"",
            cwd=str(tmp_path),
            timeout=10,
            cancel_check=cancelled.is_set,
        )
    finally:
        setter.cancel()

    assert result["ok"] is False
    assert result["executed"] is True
    assert result["cancelled"] is True
    assert result["execution_may_continue"] is True
    assert result["automatic_retry_allowed"] is False
    assert result["error_code"] == "TOOL_CANCELLED_UNCERTAIN"
    assert result["process_tree_killed"] is True


def test_queryloop_runtime_binds_cancel_callback_to_canonical_invocation():
    from agent.runtime.ssot_runtime import _make_tool_handler
    from core.runtime_engine.models import ExecutionNode, SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.tool_runtime import ToolRuntime

    seen = {}

    class FakeResult:
        status = "succeeded"
        output = {"ok": True}
        summary = ""
        artifact_ids = []
        warnings = []
        errors = []
        duration_ms = 1
        redacted = False

    class FakeClient:
        def canonicalize_arguments(self, _tool_id, arguments):
            return dict(arguments)

        def invoke(self, tool_id, arguments, *, context):
            seen["tool_id"] = tool_id
            seen["cancel_check"] = context.cancel_check
            return FakeResult()

    cancelled = threading.Event()
    runtime = ToolRuntime(SSOTRuntimeConfig(single_node_timeout_ms=1_000))
    runtime.register(
        "workspace.metadata.get",
        _make_tool_handler(
            client=FakeClient(),
            tool_id="workspace.metadata.get",
            workspace_id="ws-cancel",
            session_id="session-cancel",
            run_id="run-cancel",
            trace_id="trace-cancel",
            requested_by="agent:ssot",
        ),
    )
    ctx = StatelessContext(
        workspace_id="ws-cancel",
        session_id="session-cancel",
        request_id="request-cancel",
        user_input="verify cancellation propagation",
        extras={"cancel_check": cancelled.is_set},
    )

    result = asyncio.run(
        runtime.execute_node(
            ExecutionNode(
                id="call-cancel",
                tool="workspace.metadata.get",
                args={},
            ),
            ctx,
            {},
        )
    )

    assert result.success is True
    assert seen["tool_id"] == "workspace.metadata.get"
    assert callable(seen["cancel_check"])
    assert seen["cancel_check"]() is False


def test_tool_runtime_does_not_start_handler_after_cancellation():
    from core.runtime_engine.models import ExecutionNode, SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.tool_runtime import ToolRuntime

    invoked = []
    runtime = ToolRuntime(SSOTRuntimeConfig(single_node_timeout_ms=1_000))
    runtime.register("workspace.metadata.get", lambda _args: invoked.append(True) or {"ok": True})
    ctx = StatelessContext(
        workspace_id="ws-cancel",
        session_id="session-cancel",
        request_id="request-cancel-before-start",
        user_input="verify cancellation preflight",
        extras={"cancel_check": lambda: True},
    )

    result = asyncio.run(
        runtime.execute_node(
            ExecutionNode(id="cancel-before-start", tool="workspace.metadata.get", args={}),
            ctx,
            {},
        )
    )

    assert result.success is False
    assert result.error_code == "TOOL_CANCELLED"
    assert result.data["executed"] is False
    assert invoked == []


def test_streaming_result_preserves_handler_uncertainty_for_operation_ledger():
    from core.runtime_engine.models import ToolResult
    from core.runtime_engine.query_loop import StreamingToolExecutor

    result = ToolResult(
        node_id="cancelled-write",
        tool="exec.run",
        success=False,
        data={
            "ok": False,
            "executed": True,
            "execution_may_continue": True,
            "error_code": "TOOL_CANCELLED_UNCERTAIN",
        },
        error="command cancelled after start; outcome requires verification",
        error_code="TOOL_CANCELLED_UNCERTAIN",
    )

    streaming = StreamingToolExecutor._from_tool_result(
        result,
        fallback_call_id="cancelled-write",
    )

    assert streaming.execution_may_continue is True
    assert streaming.output["execution_may_continue"] is True
