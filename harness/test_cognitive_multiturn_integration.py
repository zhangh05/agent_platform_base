"""Multi-turn cognitive context regression tests for the SSOT QueryLoop."""
from __future__ import annotations

import asyncio


def test_second_llm_round_sees_tool_observation_in_cognitive_projection():
    from agent.llm.schemas import LLMResponse, LLMToolCall
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.tool_runtime import ToolRuntime

    responses = [
        LLMResponse(tool_calls=[
            LLMToolCall(
                id="lookup-1",
                name="data.manage",
                arguments={"action": "parse", "text": "metric\n7"},
            ),
        ]),
        LLMResponse(content="the observation is sufficient"),
    ]
    captured_messages = []

    def invoke(**kwargs):
        captured_messages.append(list(kwargs["messages"]))
        return responses.pop(0)

    config = SSOTRuntimeConfig(max_query_loop_iterations=3)
    tool_runtime = ToolRuntime(config)
    tool_runtime.register("data.manage", lambda arguments: {"ok": True, "rows": [{"metric": 7}]})
    registry = {
        "data.manage": {
            "description": "parse data",
            "args_schema": {
                "type": "object",
                "required": ["action"],
                "properties": {"action": {"type": "string"}},
            },
        },
    }
    engine = SSOTRuntimeEngine(
        config=config,
        llm_invoke=invoke,
        tool_registry=registry,
        tool_runtime=tool_runtime,
    )

    result = asyncio.run(engine.run("inspect the metric", workspace_id="default", session_id="multiturn"))

    assert result.success is True
    assert len(captured_messages) == 2
    second_cognitive = next(
        str(message.content)
        for message in captured_messages[1]
        if 'source_kind="cognitive_state"' in str(message.content)
    )
    assert '"known_fact_count":1' in second_cognitive
    assert '"unknown_count":0' in second_cognitive
