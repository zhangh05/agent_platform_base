"""SSOT Runtime should not have a fast-path bypass.

Every user turn goes through the same tool-visible QueryLoop path. The LLM can
still answer directly, but it makes that decision while seeing the tool catalog.
"""

from __future__ import annotations

import asyncio
from unittest import mock

from core.runtime_engine import SSOTRuntimeConfig, SSOTRuntimeEngine


def test_greeting_uses_tool_visible_query_loop():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "你好，我在。"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(user_input="你好", workspace_id="test"))

    assert result.success
    assert result.final_response == "你好，我在。"
    assert result.metadata.get("planner_skipped") is False
    assert "fast_path" not in result.metadata
    assert "direct_answer_latency_ms" not in result.metadata
    assert calls
    assert calls[0].get("extra", {}).get("stream_scope") == "planner"


def test_ordinary_question_uses_tool_visible_query_loop():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "NAT 是网络地址转换。"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(user_input="NAT 是什么", workspace_id="test"))

    assert result.success
    assert result.metadata.get("planner_skipped") is False
    assert "fast_path" not in result.metadata
    assert calls
    assert calls[0].get("tools") is not None


def test_conversation_followup_stays_tool_visible_with_history():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "上一轮是在解释工具结果。"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(
        user_input="什么意思？",
        workspace_id="test",
        extras={
            "conversation_history_block": (
                "RECENT CONVERSATION HISTORY:\n"
                "  [1] user: 检查工作区。\n"
                "  [2] assistant: 工具执行完成。"
            )
        },
    ))

    assert result.success
    assert result.metadata.get("planner_skipped") is False
    assert result.metadata.get("conversation_history_used") is True
    assert "fast_path" not in result.metadata
    assert calls
    assert "RECENT CONVERSATION HISTORY" in calls[0].get("user", "")
