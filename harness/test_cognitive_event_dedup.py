"""Event ordering coverage for multi-turn CognitiveState updates."""
from __future__ import annotations

import asyncio


def test_tool_evidence_event_is_emitted_once_before_second_llm_round():
    from agent.llm.schemas import LLMResponse, LLMToolCall
    from core.runtime_engine.budget_controller import BudgetController
    from core.runtime_engine.cognitive_events import COGNITIVE_EVIDENCE_REGISTERED
    from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.query_loop import QueryLoop
    from core.runtime_engine.tool_runtime import ToolRuntime

    class Emitter:
        def __init__(self):
            self.events = []

        def emit(self, event_type, payload):
            self.events.append((event_type, payload))

    responses = [
        LLMResponse(tool_calls=[
            LLMToolCall(
                id="read-1",
                name="data.manage",
                arguments={"action": "parse", "text": "item\n1"},
            ),
        ]),
        LLMResponse(content="complete"),
    ]
    evidence_event_count_before_second_call = []
    emitter = Emitter()

    def invoke(**_kwargs):
        if len(responses) == 1:
            evidence_event_count_before_second_call.append(sum(
                1 for event_type, _ in emitter.events
                if event_type == COGNITIVE_EVIDENCE_REGISTERED
            ))
        return responses.pop(0)

    config = SSOTRuntimeConfig(max_query_loop_iterations=3)
    runtime = ToolRuntime(config)
    runtime.register("data.manage", lambda _args: {"ok": True, "rows": [{"item": 1}]})
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
    loop = QueryLoop(config, registry, runtime, llm_invoke=invoke, emitter=emitter)
    context = StatelessContext(
        workspace_id="default",
        session_id="event-dedup",
        request_id="event-dedup-request",
        user_input="read an item",
    )

    asyncio.run(loop.run(context, BudgetController(config), None))

    assert evidence_event_count_before_second_call == [1]
    assert sum(
        1 for event_type, _ in emitter.events
        if event_type == COGNITIVE_EVIDENCE_REGISTERED
    ) == 1
