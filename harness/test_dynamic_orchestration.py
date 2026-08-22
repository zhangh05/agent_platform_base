from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from agent.llm.schemas import LLMToolCall
from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
from core.runtime_engine.orchestration import OrchestrationError, StepEvidence, validate_incremental_graph
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


def test_failed_step_can_be_replanned_with_same_stable_id_and_changed_args():
    class Runtime:
        def invoke_raw(self, _tool_id, arguments):
            if arguments.get("text") == "bad":
                return {"ok": False, "error": "bad input"}
            return {"ok": True, "text": arguments["text"]}

    ctx = _ctx()
    executor = StreamingToolExecutor(Runtime(), SSOTRuntimeConfig())
    first = [LLMToolCall(
        id="call-1", name="text.analyze",
        arguments={"action": "match", "text": "bad"}, step_id="inspect",
    )]
    assert asyncio.run(executor.execute(first, ctx=ctx))[0].ok is False
    second = [LLMToolCall(
        id="call-2", name="text.analyze",
        arguments={"action": "match", "text": "corrected"}, step_id="inspect",
    )]
    result = asyncio.run(executor.execute(second, ctx=ctx))[0]
    assert result.ok is True
    assert ctx.extras["orchestration_evidence"]["inspect"].output["text"] == "corrected"


def test_successful_step_id_cannot_be_replayed():
    prior = {
        "done": StepEvidence("done", "call-1", "text.analyze", True, {"ok": True}),
    }
    call = LLMToolCall(
        id="call-2", name="text.analyze",
        arguments={"action": "match", "text": "x"}, step_id="done",
    )
    with pytest.raises(OrchestrationError, match="already succeeded"):
        validate_incremental_graph([call], prior)


def test_failed_dependency_is_not_executed():
    calls_seen = []

    class Runtime:
        def invoke_raw(self, tool_id, arguments):
            calls_seen.append(arguments["action"])
            return {"ok": False, "error": "source failed"}

    calls = [
        LLMToolCall(id="a", name="data.manage", arguments={"action": "parse", "text": "x"}, step_id="source"),
        LLMToolCall(id="b", name="data.manage", arguments={"action": "stats"}, step_id="analyse", depends_on=["source"], result_bindings={"rows": "steps.source.output.rows"}),
        LLMToolCall(id="c", name="data.manage", arguments={"action": "stats"}, step_id="summarise", depends_on=["source"], result_bindings={"rows": "steps.source.output.rows"}),
    ]
    ctx = _ctx()
    results = asyncio.run(StreamingToolExecutor(Runtime(), SSOTRuntimeConfig()).execute(calls, ctx=ctx))
    assert calls_seen == ["parse"]
    assert results[1].output["error_code"] == "DEPENDENCY_FAILED"
    assert results[2].output["error_code"] == "DEPENDENCY_FAILED"
    assert ctx.extras["orchestration_batches"][0]["parallel_steps"] == [[], []]


def test_bound_arguments_are_revalidated_before_handler_execution():
    seen = []

    class Runtime:
        def invoke_raw(self, tool_id, arguments):
            seen.append((tool_id, dict(arguments)))
            if tool_id == "data.manage":
                return {"ok": True, "rows": ["not text"]}
            return {"ok": True}

    calls = [
        LLMToolCall(
            id="a", name="data.manage",
            arguments={"action": "parse", "text": "x"}, step_id="source",
        ),
        LLMToolCall(
            id="b", name="text.analyze", arguments={"action": "match"},
            step_id="consume", depends_on=["source"],
            result_bindings={"text": "steps.source.output.rows"},
        ),
    ]
    results = asyncio.run(StreamingToolExecutor(
        Runtime(), SSOTRuntimeConfig(), tool_registry={}
    ).execute(calls, ctx=_ctx()))
    assert seen == [("data.manage", {"action": "parse", "text": "x"})]
    assert results[1].output["error_code"] == "RESULT_BINDING_INVALID"


def test_execution_budget_rejects_oversized_batch_before_any_handler_runs():
    from core.runtime_engine.budget_controller import BudgetController

    seen = []

    class Runtime:
        def invoke_raw(self, _tool_id, arguments):
            seen.append(arguments)
            return {"ok": True}

    config = SSOTRuntimeConfig(max_nodes=1)
    calls = [
        LLMToolCall(id="a", name="data.manage", arguments={"action": "parse", "text": "a"}, step_id="a"),
        LLMToolCall(id="b", name="data.manage", arguments={"action": "parse", "text": "b"}, step_id="b"),
    ]
    results = asyncio.run(StreamingToolExecutor(Runtime(), config).execute(
        calls, ctx=_ctx(), budget=BudgetController(config),
    ))
    assert seen == []
    assert {result.output["error_code"] for result in results} == {"TOOL_NODES_EXCEEDED"}


def test_queryloop_replans_oversized_model_round_before_any_handler_runs():
    from agent.llm.schemas import LLMResponse
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.tool_runtime import ToolRuntime

    responses = [
        LLMResponse(tool_calls=[
            LLMToolCall(
                id=f"too-many-{index}", name="data.manage",
                arguments={"action": "parse", "text": str(index)},
            )
            for index in range(25)
        ]),
        LLMResponse(tool_calls=[LLMToolCall(
            id="bounded", name="data.manage",
            arguments={"action": "parse", "text": "bounded"},
        )]),
        LLMResponse(content="已完成有界处理"),
    ]
    received = []
    prompts = []

    def llm(**kwargs):
        prompts.append(kwargs["messages"])
        return responses.pop(0)

    def handler(arguments):
        received.append(dict(arguments))
        return {"ok": True, "text": arguments["text"]}

    config = SSOTRuntimeConfig(
        max_query_loop_iterations=4,
        max_tool_calls_per_iteration=2,
    )
    runtime = ToolRuntime(config)
    runtime.register("data.manage", handler)
    registry = {"data.manage": {
        "description": "data",
        "args_schema": {
            "type": "object", "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": ["parse"]},
                "text": {"type": "string"},
            },
        },
    }}
    engine = SSOTRuntimeEngine(
        config=config, llm_invoke=llm, tool_registry=registry, tool_runtime=runtime,
    )

    result = asyncio.run(engine.run(
        "bounded work", workspace_id="default", session_id="session",
    ))

    assert result.success is True
    assert result.final_response == "已完成有界处理"
    assert received == [{"action": "parse", "text": "bounded"}]
    assert result.metadata["batch_replans"] == 1
    assert any("RUNTIME PLAN BOUNDARY" in message.content for message in prompts[1])


def test_read_only_parallel_timeout_is_failed_not_unknown():
    from core.runtime_engine.budget_controller import BudgetController

    class Runtime:
        def invoke_raw(self, _tool_id, _arguments):
            time.sleep(0.08)
            return {"ok": True}

    config = SSOTRuntimeConfig(
        max_total_seconds=5,
        max_tool_seconds=5,
        parallel_layer_timeout_ms=20,
    )
    calls = [LLMToolCall(
        id="slow-read", name="data.manage",
        arguments={"action": "parse", "text": "x"},
    )]
    result = asyncio.run(StreamingToolExecutor(Runtime(), config).execute(
        calls, ctx=_ctx(), budget=BudgetController(config),
    ))[0]

    assert result.ok is False
    assert result.error_code == "PARALLEL_LAYER_TIMEOUT"
    assert result.execution_may_continue is False


def test_execution_budget_rejects_graph_deeper_than_limit():
    from core.runtime_engine.budget_controller import BudgetController

    class Runtime:
        def invoke_raw(self, _tool_id, _arguments):
            raise AssertionError("handler must not run")

    config = SSOTRuntimeConfig(max_depth=1)
    calls = [
        LLMToolCall(id="a", name="data.manage", arguments={"action": "parse", "text": "a"}, step_id="a"),
        LLMToolCall(id="b", name="data.manage", arguments={"action": "stats"}, step_id="b", depends_on=["a"]),
    ]
    results = asyncio.run(StreamingToolExecutor(Runtime(), config).execute(
        calls, ctx=_ctx(), budget=BudgetController(config),
    ))
    assert results[0].output["error_code"] == "TOOL_DEPTH_EXCEEDED"


def test_execution_depth_is_enforced_across_incremental_rounds():
    from core.runtime_engine.budget_controller import BudgetController

    class Runtime:
        def invoke_raw(self, _tool_id, _arguments):
            return {"ok": True, "text": "ok"}

    config = SSOTRuntimeConfig(max_depth=1)
    budget = BudgetController(config)
    ctx = _ctx()
    executor = StreamingToolExecutor(Runtime(), config)
    first = [LLMToolCall(
        id="a", name="text.analyze",
        arguments={"action": "match", "text": "a"}, step_id="a",
    )]
    assert asyncio.run(executor.execute(first, ctx=ctx, budget=budget))[0].ok is True
    second = [LLMToolCall(
        id="b", name="text.analyze",
        arguments={"action": "match", "text": "b"},
        step_id="b", depends_on=["a"],
    )]
    result = asyncio.run(executor.execute(second, ctx=ctx, budget=budget))[0]
    assert result.output["error_code"] == "TOOL_DEPTH_EXCEEDED"


def test_bounded_read_group_is_not_rejected_for_topological_width():
    from core.runtime_engine.budget_controller import BudgetController

    class Runtime:
        def invoke_raw(self, _tool_id, _arguments):
            return {"ok": True}

    config = SSOTRuntimeConfig(max_layer_concurrency=2, max_global_concurrency=2)
    calls = [
        LLMToolCall(
            id=f"call-{index}", name="data.manage",
            arguments={"action": "parse", "text": str(index)},
            step_id=f"step_{index}",
        )
        for index in range(10)
    ]
    results = asyncio.run(StreamingToolExecutor(Runtime(), config).execute(
        calls, ctx=_ctx(), budget=BudgetController(config),
    ))
    assert all(result.ok for result in results)


def test_independent_writes_are_serial_and_not_labelled_parallel():
    class Runtime:
        def invoke_raw(self, _tool_id, _arguments):
            return {"ok": True}

    calls = [
        LLMToolCall(
            id="a", name="workspace.file",
            arguments={"action": "write", "filename": "a.txt", "content": "a"},
            step_id="write_a",
        ),
        LLMToolCall(
            id="b", name="workspace.file",
            arguments={"action": "write", "filename": "b.txt", "content": "b"},
            step_id="write_b",
        ),
    ]
    results = asyncio.run(StreamingToolExecutor(
        Runtime(), SSOTRuntimeConfig(),
    ).execute(calls, ctx=_ctx()))
    assert [result.output["_orchestration"]["parallel"] for result in results] == [False, False]


def test_orchestration_evidence_respects_total_budget():
    class Runtime:
        def invoke_raw(self, _tool_id, _arguments):
            return {"ok": True, "text": "x" * 20_000}

    config = SSOTRuntimeConfig(
        max_orchestration_step_tokens=40,
        max_orchestration_evidence_tokens=40,
    )
    ctx = _ctx()
    call = LLMToolCall(
        id="a", name="text.analyze",
        arguments={"action": "match", "text": "x"}, step_id="source",
    )
    asyncio.run(StreamingToolExecutor(Runtime(), config).execute([call], ctx=ctx))
    projection = ctx.extras["orchestration_evidence"]["source"].output
    assert projection["_evidence_projection"]["truncated"] is True


def test_sync_handler_does_not_block_event_loop_and_uncertain_timeout_is_not_retried():
    from core.runtime_engine.models import ExecutionNode
    from core.runtime_engine.tool_runtime import ToolRuntime

    config = SSOTRuntimeConfig(single_node_timeout_ms=25, max_retries_per_node=1)
    runtime = ToolRuntime(config)
    calls = {"count": 0}

    def slow_handler(_arguments):
        calls["count"] += 1
        time.sleep(0.08)
        return {"ok": True}

    runtime.register("data.manage", slow_handler)

    async def scenario():
        ticked = False

        async def ticker():
            nonlocal ticked
            await asyncio.sleep(0.01)
            ticked = True

        result, _ = await asyncio.gather(
            runtime.execute_node(
                ExecutionNode(id="n", tool="data.manage", args={"action": "parse", "text": "x"}),
                _ctx(), {},
            ),
            ticker(),
        )
        assert ticked is True
        assert result.error_code == "TOOL_TIMEOUT_UNCERTAIN"
        assert result.metadata["execution_may_continue"] is True
        await asyncio.sleep(0.09)

    asyncio.run(scenario())
    assert calls["count"] == 1


def test_stop_binding_failure_prevents_same_layer_execution():
    calls_seen = []

    class Runtime:
        def invoke_raw(self, tool_id, arguments):
            calls_seen.append(arguments["action"])
            if arguments["action"] == "source":
                return {"ok": True, "text": "seed"}
            return {"ok": True}

    calls = [
        LLMToolCall(id="source", name="data.manage", arguments={"action": "source"}, step_id="source"),
        LLMToolCall(id="independent", name="data.manage", arguments={"action": "independent"}, step_id="independent", depends_on=["source"]),
        LLMToolCall(
            id="fatal-binding", name="data.manage", arguments={"action": "fatal"},
            step_id="fatal-binding", depends_on=["source"],
            result_bindings={"text": "steps.source.output.missing"}, failure_policy="stop",
        ),
    ]
    results = asyncio.run(StreamingToolExecutor(Runtime(), SSOTRuntimeConfig()).execute(calls, ctx=_ctx()))
    assert calls_seen == ["source"]
    assert results[1].output["error_code"] == "PLAN_STOPPED"
    assert results[2].output["error_code"] == "RESULT_BINDING_FAILED"


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
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_RUNTIME_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LZCORE_TRUSTED_LOCAL_PYTHON_EXECUTION", "true")
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
    model_messages = []

    def llm(**kwargs):
        model_messages.append(list(kwargs["messages"]))
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
    assert [message.role for message in model_messages[1]][-3:] == ["assistant", "tool", "tool"]
    assert model_messages[1][-2].tool_call_id == "provider-a"
    assert model_messages[1][-1].tool_call_id == "provider-b"


def test_read_dedupe_key_changes_after_a_successful_mutation():
    from core.runtime_engine.query_loop import QueryLoop

    loop = QueryLoop(
        SSOTRuntimeConfig(),
        {"workspace.file": {"description": "file", "args_schema": {}}},
        SimpleNamespace(),
    )
    read = LLMToolCall(
        id="read", name="workspace.file",
        arguments={"action": "read", "filepath": "result.txt"},
    )
    write = LLMToolCall(
        id="write", name="workspace.file",
        arguments={"action": "write", "filename": "result.txt", "content": "new"},
    )
    assert loop._completion_key(read, 0) != loop._completion_key(read, 1)
    assert loop._completion_key(write, 0) == loop._completion_key(write, 1)


def test_repeated_mutation_is_not_replayed_when_mixed_with_a_new_read():
    from agent.llm.schemas import LLMResponse
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.tool_runtime import ToolRuntime

    responses = [
        LLMResponse(tool_calls=[LLMToolCall(
            id="write-1", name="workspace.file",
            arguments={"action": "write", "filename": "state.txt", "content": "ready"},
        )]),
        LLMResponse(tool_calls=[
            LLMToolCall(
                id="write-2", name="workspace.file",
                arguments={"action": "write", "filename": "state.txt", "content": "ready"},
            ),
            LLMToolCall(
                id="read-1", name="workspace.file",
                arguments={"action": "list"},
            ),
        ]),
    ]
    received = []

    def llm(**_kwargs):
        return responses.pop(0)

    def handler(arguments):
        received.append(dict(arguments))
        return {"ok": True}

    config = SSOTRuntimeConfig(max_query_loop_iterations=4)
    runtime = ToolRuntime(config)
    runtime.register("workspace.file", handler)
    registry = {
        "workspace.file": {
            "description": "files",
            "args_schema": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["list", "write"]},
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }
    engine = SSOTRuntimeEngine(
        config=config, llm_invoke=llm, tool_registry=registry, tool_runtime=runtime,
    )
    result = asyncio.run(engine.run(
        "write once", workspace_id="default", session_id="session",
    ))

    assert received == [{"action": "write", "filename": "state.txt", "content": "ready"}]
    assert result.success is False
    assert "duplicate_mutation_call" in result.errors


def test_plain_json_plan_text_is_not_an_alternate_tool_call_path():
    from agent.llm.schemas import LLMResponse
    from core.runtime_engine.query_loop import QueryLoop

    loop = QueryLoop(SSOTRuntimeConfig(), {}, SimpleNamespace())
    raw = '{"nodes":[{"tool":"system.manage","args":{"action":"health"}}]}'
    response = loop._coerce_llm_response(LLMResponse(content=raw))
    assert response.content == raw
    assert response.tool_calls == []


def test_saved_workflow_runs_independent_reads_in_parallel(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    import workflows.service as service

    lock = threading.Lock()
    active = 0
    max_active = 0

    class Client:
        def canonicalize_arguments(self, _tool_id, arguments):
            return dict(arguments)

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


def test_uncertain_write_fences_later_writes_in_same_batch(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    calls_seen = []

    class Runtime:
        def invoke_raw(self, _tool_id, arguments):
            calls_seen.append(arguments["filename"])
            return {
                "ok": False,
                "error_code": "TOOL_TIMEOUT_UNCERTAIN",
                "error": "remote write may still be running",
                "execution_may_continue": True,
            }

    ctx = _ctx()
    calls = [
        LLMToolCall(
            id="write-a", name="workspace.file",
            arguments={"action": "write", "filename": "a.txt", "content": "a"},
            step_id="write_a",
        ),
        LLMToolCall(
            id="write-b", name="workspace.file",
            arguments={"action": "write", "filename": "b.txt", "content": "b"},
            step_id="write_b",
        ),
    ]

    results = asyncio.run(
        StreamingToolExecutor(Runtime(), SSOTRuntimeConfig()).execute(calls, ctx=ctx)
    )

    assert calls_seen == ["a.txt"]
    assert results[0].execution_may_continue is True
    assert results[1].output["error_code"] == "WRITE_BLOCKED_BY_UNKNOWN_OUTCOME"
    assert results[1].output["unknown_outcome_trigger"]["call_id"] == "write-a"
    outcome_key = "unknown" + "_outcome"
    assert ctx.extras[outcome_key]["status"] == "unknown"
    assert ctx.extras[outcome_key]["call_id"] == "write-a"
    from core.runtime_engine.operation_ledger import operation_id
    from storage.records import workspace_record_file
    blocked_id = operation_id("default", ctx.request_id, "write-b")
    blocked_path = workspace_record_file("default", "operations", f"{blocked_id}.json")
    blocked = __import__("json").loads(blocked_path.read_text())
    assert blocked["status"] == "blocked"


def test_uncertain_read_does_not_install_write_fence(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    calls_seen = []

    class Runtime:
        def invoke_raw(self, tool_id, arguments):
            calls_seen.append((tool_id, arguments.get("action")))
            if tool_id == "data.manage":
                return {
                    "ok": False,
                    "error_code": "PARALLEL_LAYER_TIMEOUT_UNCERTAIN",
                    "error": "read may still be running",
                    "execution_may_continue": True,
                }
            return {"ok": True}

    ctx = _ctx()
    calls = [
        LLMToolCall(
            id="read-a", name="data.manage",
            arguments={"action": "parse", "text": "x"}, step_id="read_a",
        ),
        LLMToolCall(
            id="write-b", name="workspace.file",
            arguments={"action": "write", "filename": "b.txt", "content": "b"},
            step_id="write_b",
        ),
    ]

    results = asyncio.run(
        StreamingToolExecutor(Runtime(), SSOTRuntimeConfig()).execute(calls, ctx=ctx)
    )

    assert calls_seen == [("data.manage", "parse"), ("workspace.file", "write")]
    assert "unknown_outcome" not in ctx.extras
    assert results[1].ok is True


def test_unstarted_write_budget_exhaustion_closes_operation_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))

    class Runtime:
        def invoke_raw(self, _tool_id, _arguments):
            raise AssertionError("budget-exhausted write must not start")

    class ExhaustedBudget:
        @staticmethod
        def remaining_execution_seconds():
            return 0.0

    ctx = _ctx()
    call = LLMToolCall(
        id="write-budget", name="workspace.file",
        arguments={"action": "write", "filename": "a.txt", "content": "a"},
        step_id="write_budget",
    )
    executor = StreamingToolExecutor(Runtime(), SSOTRuntimeConfig())
    result = asyncio.run(executor._execute_independent_calls(
        [call], ctx=ctx, budget=ExhaustedBudget(),
    ))[0]
    assert result.error_code == "TOOL_BUDGET_EXHAUSTED"

    from core.runtime_engine.operation_ledger import operation_id
    from storage.records import workspace_record_file
    op_id = operation_id("default", ctx.request_id, call.id)
    record = __import__("json").loads(
        workspace_record_file("default", "operations", f"{op_id}.json").read_text()
    )
    assert record["status"] == "blocked"


def test_explicit_individual_weather_calls_disable_batch_compilation():
    from agent.llm.schemas import LLMToolCall
    from core.runtime_engine.batch_compiler import (
        compile_batchable_calls,
        user_requires_individual_tool_calls,
    )

    registry = {
        "web.manage": {"metadata": {"batching": [{
            "source_action": "weather",
            "target_action": "weather_batch",
            "group_by": ["days"],
            "collect_arg": "location",
            "collection_arg": "locations",
            "max_batch_size": 10,
        }]}}
    }
    calls = [
        LLMToolCall(id=f"city-{index}", name="web.manage", arguments={
            "action": "weather", "location": f"城市{index}", "days": 10,
        })
        for index in range(3)
    ]

    assert user_requires_individual_tool_calls("每个城市都必须使用独立调用。") is True
    compiled, events = compile_batchable_calls(calls, registry, allow_batching=False)
    assert compiled == calls
    assert events == []


def test_queryloop_replans_explicit_weather_batch_before_any_handler_runs():
    import asyncio

    from agent.llm.schemas import LLMResponse, LLMToolCall
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.tool_runtime import ToolRuntime

    responses = [
        LLMResponse(tool_calls=[LLMToolCall(
            id="illegal-batch", name="web.manage", arguments={
                "action": "weather_batch", "locations": ["上海", "南京"], "days": 10,
            },
        )]),
        LLMResponse(tool_calls=[
            LLMToolCall(id="city-a", name="web.manage", arguments={
                "action": "weather", "location": "上海", "days": 10,
            }),
            LLMToolCall(id="city-b", name="web.manage", arguments={
                "action": "weather", "location": "南京", "days": 10,
            }),
        ]),
        LLMResponse(content="已按单城市调用返回结果。"),
    ]
    prompts = []
    received = []

    def llm(**kwargs):
        prompts.append(kwargs["messages"])
        return responses.pop(0)

    def handler(arguments):
        received.append(dict(arguments))
        return {"ok": True, "source_type": "structured_weather", "forecast_daily": []}

    config = SSOTRuntimeConfig(max_query_loop_iterations=5, max_tool_calls_per_iteration=2)
    runtime = ToolRuntime(config)
    runtime.register("web.manage", handler)
    registry = {"web.manage": {
        "description": "weather",
        "args_schema": {"type": "object", "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["weather", "weather_batch"]},
            "location": {"type": "string"}, "locations": {"type": "array"},
            "days": {"type": "integer"},
        }},
        "metadata": {"batching": [{
            "source_action": "weather", "target_action": "weather_batch",
            "group_by": ["days"], "collect_arg": "location",
            "collection_arg": "locations", "max_batch_size": 10,
        }]},
    }}
    engine = SSOTRuntimeEngine(
        config=config, llm_invoke=llm, tool_registry=registry, tool_runtime=runtime,
    )

    result = asyncio.run(engine.run(
        "每个城市都必须使用独立调用。请查询上海和南京未来十天天气，并逐日返回。",
        workspace_id="default", session_id="individual-weather",
    ))

    assert result.success is True
    assert [item["action"] for item in received] == ["weather", "weather"]
    assert all("locations" not in item for item in received)
    assert any("不得使用 batch action" in message.content for message in prompts[1])


def test_explicit_individual_calls_reject_all_batch_action_names():
    from agent.llm.schemas import LLMToolCall
    from core.runtime_engine.batch_compiler import contains_disallowed_batch_action

    calls = [LLMToolCall(
        id="location-batch",
        name="location.manage",
        arguments={"action": "resolve_batch", "queries": ["上海", "南京"]},
    )]

    assert contains_disallowed_batch_action(calls, {"location.manage": {}}) is True


def test_queryloop_replans_repairable_read_only_length_error():
    import asyncio

    from agent.llm.schemas import LLMResponse, LLMToolCall
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.tool_runtime import ToolRuntime

    responses = [
        LLMResponse(tool_calls=[LLMToolCall(
            id="too-many", name="location.manage", arguments={
                "action": "resolve_batch", "queries": [f"城市{index}" for index in range(21)],
            },
        )]),
        LLMResponse(tool_calls=[LLMToolCall(
            id="bounded", name="location.manage", arguments={
                "action": "resolve_batch", "queries": ["上海", "南京"],
            },
        )]),
        LLMResponse(content="已完成有界地点解析。"),
    ]
    received = []
    prompts = []

    def llm(**kwargs):
        prompts.append(kwargs["messages"])
        return responses.pop(0)

    def handler(arguments):
        received.append(dict(arguments))
        return {"ok": True, "locations": []}

    config = SSOTRuntimeConfig(max_query_loop_iterations=5, max_tool_calls_per_iteration=2)
    runtime = ToolRuntime(config)
    runtime.register("location.manage", handler)
    registry = {"location.manage": {
        "description": "location",
        "args_schema": {"type": "object", "required": ["action", "queries"], "properties": {
            "action": {"type": "string", "enum": ["resolve_batch"]},
            "queries": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        }},
    }}
    engine = SSOTRuntimeEngine(
        config=config, llm_invoke=llm, tool_registry=registry, tool_runtime=runtime,
    )

    result = asyncio.run(engine.run(
        "解析多个地点。",
        workspace_id="default", session_id="bounded-location",
    ))

    assert result.success is True
    assert received == [{"action": "resolve_batch", "queries": ["上海", "南京"]}]
    assert any("allows at most 20" in message.content for message in prompts[1])
