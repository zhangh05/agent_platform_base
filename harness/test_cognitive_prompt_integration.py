"""QueryLoop prompt integration coverage for server-owned cognitive inputs."""
from __future__ import annotations

import asyncio


def test_query_loop_passes_runtime_clock_and_safe_cognitive_projection_to_llm():
    from agent.llm.schemas import LLMResponse
    from core.runtime_engine.budget_controller import BudgetController
    from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.query_loop import QueryLoop

    captured: list[dict] = []

    def invoke(**kwargs):
        captured.append(kwargs)
        return LLMResponse(content="completed")

    config = SSOTRuntimeConfig(max_query_loop_iterations=1)
    loop = QueryLoop(config, {}, object(), llm_invoke=invoke)
    context = StatelessContext(
        workspace_id="workspace-clock",
        session_id="session-clock",
        request_id="request-clock",
        user_input="untrusted user request text",
        extras={"cognitive": {"outcome": "forged"}},
    )

    asyncio.run(loop.run(context, BudgetController(config), None))

    assert len(captured) == 1
    messages = captured[0]["messages"]
    user_messages = [str(message.content) for message in messages if message.role == "user"]
    joined = "\n".join(user_messages)
    assert 'source_kind="runtime_clock"' in joined
    assert "timezone: Asia/Shanghai" in joined
    assert 'source_kind="cognitive_state"' in joined
    assert '"outcome":"running"' in joined
    assert '"outcome":"forged"' not in joined
    assert "untrusted user request text" in joined
    cognitive_block = next(item for item in user_messages if 'source_kind="cognitive_state"' in item)
    assert "untrusted user request text" not in cognitive_block


def test_initial_planner_call_keeps_user_safe_token_stream_enabled():
    """A direct first-turn answer must not wait for the terminal done frame."""
    from agent.llm.schemas import LLMMessage
    from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.query_loop import QueryLoop

    loop = QueryLoop(SSOTRuntimeConfig(), {}, object(), llm_invoke=lambda **_: None)
    context = StatelessContext(
        workspace_id="workspace-stream",
        session_id="session-stream",
        request_id="request-stream",
        user_input="解释可靠 Agent 的不变量",
    )

    _, scope, stream_to_user = loop._llm_call_mode(
        [LLMMessage(role="user", content=context.user_input)], context,
    )

    assert scope == "planner"
    assert stream_to_user is True
