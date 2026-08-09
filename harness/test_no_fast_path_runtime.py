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


def test_speed_conversion_uses_tool_visible_query_loop():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "5295 kb/s 按小写 b 计算约为 5.29 Mbps。"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(
        user_input="5295kb/s是多少速度",
        workspace_id="test",
    ))

    assert result.success
    assert result.final_response == "5295 kb/s 按小写 b 计算约为 5.29 Mbps。"
    assert result.metadata.get("planner_skipped") is False
    assert "deterministic_answer" not in result.metadata
    assert "fast_path" not in result.metadata
    assert calls
    assert calls[0].get("tools") is not None
    assert calls[0].get("extra", {}).get("stream_scope") == "planner"


def test_short_unit_correction_uses_tool_visible_query_loop_with_history():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "对，按小写 b 理解上一轮速度。"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(
        user_input="我是小b",
        workspace_id="test",
        extras={
            "conversation_history_block": (
                "RECENT CONVERSATION HISTORY:\n"
                "  [1] user: 5295kb/s是多少速度\n"
                "  [2] assistant: 之前按大写 B 解释了。"
            )
        },
    ))

    assert result.success
    assert result.final_response == "对，按小写 b 理解上一轮速度。"
    assert result.metadata.get("planner_skipped") is False
    assert result.metadata.get("conversation_history_used") is True
    assert "deterministic_answer" not in result.metadata
    assert "fast_path" not in result.metadata
    assert calls
    assert "RECENT CONVERSATION HISTORY" in calls[0].get("user", "")
    assert calls[0].get("tools") is not None


def test_ambiguous_operational_request_still_reaches_query_loop():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "要连接哪台设备、执行什么检查？"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(
        user_input="你登录刷命令",
        workspace_id="test",
    ))

    assert result.success
    assert result.final_response == "要连接哪台设备、执行什么检查？"
    assert result.metadata.get("planner_skipped") is False
    assert result.metadata.get("query_loop") is True
    assert "requires_clarification" not in result.metadata
    assert "skip_reason" not in result.metadata
    assert calls
    assert calls[0].get("tools") is not None
    assert "<runtime_guidance trusted=\"true\">" in calls[0].get("user", "")
    assert "Potentially missing fields" in calls[0].get("user", "")


def test_response_nudge_does_not_hide_tools_from_llm():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "我会基于已有结果回答。"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(
        user_input="根据已有结果总结",
        workspace_id="test",
        extras={"response_only": True, "response_only_reason": "test"},
    ))

    assert result.success
    assert result.metadata.get("planner_skipped") is False
    assert calls
    assert calls[0].get("extra", {}).get("stream_scope") == "response"
    assert calls[0].get("tools") is not None


def test_adapter_tool_fallback_surfaces_actual_tool_output():
    from agent.runtime.ssot_runtime import (
        _final_response,
        _tool_result_fallback_from_projected_calls,
    )

    class RuntimeResult:
        final_response = "工具执行成功"

    assert _final_response(RuntimeResult()) == ""

    text = _tool_result_fallback_from_projected_calls([
        {
            "tool_id": "exec.run",
            "ok": True,
            "summary": "命令执行完成",
            "result": {
                "command": "uname -a",
                "exit_code": 0,
                "stdout": "Linux test-host 6.8.0",
            },
            "artifacts": [],
        }
    ])

    assert "服务已完成" not in text
    assert "exec.run" in text
    assert "uname -a" in text
    assert "Linux test-host" in text
