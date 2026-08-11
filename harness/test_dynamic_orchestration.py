from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from agent.llm.schemas import LLMToolCall
from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
from core.runtime_engine.orchestration import OrchestrationError, validate_incremental_graph
from core.runtime_engine.query_loop import StreamingToolExecutor


def _ctx() -> StatelessContext:
    return StatelessContext(
        workspace_id="default", session_id="session", request_id="request",
        user_input="coordinate tools",
    )


def test_queryloop_extracts_plan_metadata_without_leaking_it_to_handler_args():
    from core.runtime_engine.query_loop import QueryLoop

    loop = QueryLoop.__new__(QueryLoop)
    parsed = loop._parse_tool_calls([{
        "id": "provider-call", "name": "data__manage",
        "arguments": {
            "action": "stats", "plan_step_id": "analyse",
            "plan_depends_on": ["extract"],
            "plan_bindings": {"rows": "steps.extract.output.rows"},
            "plan_failure": "continue",
        },
    }])[0]
    assert parsed.name == "data.manage"
    assert parsed.step_id == "analyse"
    assert parsed.depends_on == ["extract"]
    assert parsed.result_bindings == {"rows": "steps.extract.output.rows"}
    assert parsed.failure_policy == "continue"
    assert "plan_step_id" not in parsed.arguments


def test_incremental_graph_resolves_prior_result_into_analysis_input():
    seen = []

    class Runtime:
        def invoke_raw(self, tool_id, arguments):
            seen.append((tool_id, dict(arguments)))
            if arguments.get("action") == "parse":
                return {"ok": True, "rows": [{"value": 7}]}
            return {"ok": True, "received": arguments.get("rows")}

    calls = [
        LLMToolCall(
            id="call-1", name="data.manage", arguments={"action": "parse", "text": "value\n7"},
            step_id="extract",
        ),
        LLMToolCall(
            id="call-2", name="data.manage", arguments={"action": "stats"},
            step_id="analyse", depends_on=["extract"],
            result_bindings={"rows": "steps.extract.output.rows"},
        ),
    ]
    results = asyncio.run(StreamingToolExecutor(Runtime(), SSOTRuntimeConfig()).execute(calls, ctx=_ctx()))

    assert [result.ok for result in results] == [True, True]
    assert seen[1][1]["rows"] == [{"value": 7}]
    assert results[1].output["_orchestration"]["depends_on"] == ["extract"]


def test_incremental_graph_keeps_evidence_across_queryloop_rounds():
    class Runtime:
        def invoke_raw(self, tool_id, arguments):
            return {"ok": True, "text": arguments.get("text", "source")}

    ctx = _ctx()
    executor = StreamingToolExecutor(Runtime(), SSOTRuntimeConfig())
    first = [LLMToolCall(
        id="call-1", name="text.analyze", arguments={"action": "match", "text": "source"},
        step_id="source",
    )]
    asyncio.run(executor.execute(first, ctx=ctx))
    second = [LLMToolCall(
        id="call-2", name="text.analyze", arguments={"action": "match", "pattern": "source"},
        step_id="verify", depends_on=["source"],
        result_bindings={"text": "steps.source.output.text"},
    )]
    result = asyncio.run(executor.execute(second, ctx=ctx))[0]
    assert result.ok is True
    assert result.output["text"] == "source"


def test_failed_dependency_is_not_executed():
    calls_seen = []

    class Runtime:
        def invoke_raw(self, tool_id, arguments):
            calls_seen.append(arguments["action"])
            return {"ok": False, "error": "source failed"}

    calls = [
        LLMToolCall(id="a", name="data.manage", arguments={"action": "parse", "text": "x"}, step_id="source"),
        LLMToolCall(id="b", name="data.manage", arguments={"action": "stats"}, step_id="analyse", depends_on=["source"], result_bindings={"rows": "steps.source.output.rows"}),
    ]
    results = asyncio.run(StreamingToolExecutor(Runtime(), SSOTRuntimeConfig()).execute(calls, ctx=_ctx()))
    assert calls_seen == ["parse"]
    assert results[1].output["error_code"] == "DEPENDENCY_FAILED"


def test_stop_failure_policy_prevents_later_layers():
    calls_seen = []

    class Runtime:
        def invoke_raw(self, tool_id, arguments):
            calls_seen.append(arguments["action"])
            return {"ok": False, "error": "stop here"}

    calls = [
        LLMToolCall(id="a", name="data.manage", arguments={"action": "parse", "text": "x"}, step_id="source", failure_policy="stop"),
        LLMToolCall(id="b", name="data.manage", arguments={"action": "stats", "rows": []}, step_id="analyse", depends_on=["source"]),
        LLMToolCall(id="c", name="text.analyze", arguments={"action": "match", "text": "x"}, step_id="verify", depends_on=["analyse"]),
    ]
    results = asyncio.run(StreamingToolExecutor(Runtime(), SSOTRuntimeConfig()).execute(calls, ctx=_ctx()))
    assert calls_seen == ["parse"]
    assert [result.output["error_code"] for result in results[1:]] == ["PLAN_STOPPED", "PLAN_STOPPED"]


def test_unsafe_result_binding_is_rejected_before_execution():
    call = LLMToolCall(
        id="b", name="workspace.file", arguments={"action": "write", "filename": "x.txt"},
        step_id="write", depends_on=["source"],
        result_bindings={"content": "steps.source.output.text"},
    )
    with pytest.raises(OrchestrationError, match="unsafe binding target"):
        validate_incremental_graph([call], {"source": SimpleNamespace(ok=True)})


def test_independent_reads_honor_parallel_width():
    lock = threading.Lock()
    active = 0
    max_active = 0

    class Runtime:
        def invoke_raw(self, tool_id, arguments):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.04)
            with lock:
                active -= 1
            return {"ok": True}

    calls = [
        LLMToolCall(id=f"call-{i}", name="data.manage", arguments={"action": "parse", "text": str(i)}, step_id=f"step_{i}")
        for i in range(4)
    ]
    config = SSOTRuntimeConfig(max_layer_concurrency=2)
    asyncio.run(StreamingToolExecutor(Runtime(), config).execute(calls, ctx=_ctx()))
    assert max_active == 2


def test_python_bridge_accepts_structured_input_and_returns_structured_result(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.tools.python_exec import execute_python_code

    outcome = execute_python_code(
        "result = {'total': sum(item['value'] for item in input_data['rows'])}",
        workspace_id="default", run_id="run", input_data={"rows": [{"value": 2}, {"value": 5}]},
    )
    assert outcome["ok"] is True
    assert outcome["structured_output"] == {"total": 7}
    assert "__LIANZHI_STRUCTURED__" not in outcome["stdout"]


def test_engine_executes_dependent_tool_group_then_synthesizes():
    from agent.llm.schemas import LLMResponse
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.tool_runtime import ToolRuntime

    responses = [
        LLMResponse(tool_calls=[
            LLMToolCall(
                id="provider-a", name="data.manage",
                arguments={"action": "parse", "text": "value\n7"},
                step_id="extract",
            ),
            LLMToolCall(
                id="provider-b", name="data.manage",
                arguments={"action": "stats"}, step_id="analyse",
                depends_on=["extract"],
                result_bindings={"rows": "steps.extract.output.rows"},
            ),
        ]),
        LLMResponse(content="分析完成"),
    ]
    received = []

    def llm(**_kwargs):
        return responses.pop(0)

    def handler(arguments):
        received.append(dict(arguments))
        if arguments["action"] == "parse":
            return {"ok": True, "rows": [{"value": 7}]}
        return {"ok": True, "count": len(arguments["rows"])}

    config = SSOTRuntimeConfig(max_query_loop_iterations=4)
    runtime = ToolRuntime(config)
    runtime.register("data.manage", handler)
    registry = {
        "data.manage": {
            "description": "data", "args_schema": {
                "type": "object", "required": ["action"],
                "properties": {"action": {"type": "string", "enum": ["parse", "stats"]}},
            },
        },
    }
    engine = SSOTRuntimeEngine(config=config, llm_invoke=llm, tool_registry=registry, tool_runtime=runtime)
    result = asyncio.run(engine.run("analyse", workspace_id="default", session_id="session"))

    assert result.success is True
    assert result.final_response == "分析完成"
    assert received[1]["rows"] == [{"value": 7}]
    assert result.metadata["orchestration_batches"][0]["layers"] == [["extract"], ["analyse"]]


def test_saved_workflow_runs_independent_reads_in_parallel(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    import workflows.service as service

    lock = threading.Lock()
    active = 0
    max_active = 0

    class Client:
        def list_tools(self):
            return [{"tool_id": "data.manage", "enabled": True}]

        def invoke(self, tool_id, arguments, context=None):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.04)
            with lock:
                active -= 1
            return SimpleNamespace(
                status="succeeded", output={"value": arguments["text"]}, summary="ok",
                errors=[], warnings=[], duration_ms=40, artifact_ids=[], redacted=True,
            )

    client = Client()
    monkeypatch.setattr(service, "_tool_client", lambda: client)
    service.save_workflow("default", {
        "workflow_id": "parallel_reads", "name": "parallel reads",
        "nodes": [
            {"node_id": "a", "tool_id": "data.manage", "arguments": {"action": "parse", "text": "a"}},
            {"node_id": "b", "tool_id": "data.manage", "arguments": {"action": "parse", "text": "b"}},
        ],
    })
    run = service.execute_workflow("default", "parallel_reads")
    assert run["status"] == "succeeded"
    assert max_active == 2
    assert all(node["orchestration"]["parallel"] for node in run["nodes"])
