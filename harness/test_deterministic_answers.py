from __future__ import annotations

import asyncio
from unittest import mock

from core.runtime_engine import SSOTRuntimeConfig, SSOTRuntimeEngine
from core.runtime_engine.deterministic_answer import answer_deterministically


def test_speed_conversion_lowercase_b_is_bits():
    answer = answer_deterministically("5295kb/s是多少速度")

    assert answer is not None
    assert answer.route == "deterministic_speed_unit_conversion"
    assert "小写 b 表示 bit" in answer.response
    assert "5.29 Mbps" in answer.response
    assert "662 KB/s" in answer.response
    assert "646 KiB/s" in answer.response


def test_speed_followup_small_b_uses_prior_number_from_history():
    history = (
        "RECENT CONVERSATION HISTORY:\n"
        "  [1] user: 5295kb/s是多少速度\n"
        "  [2] assistant: 之前误按 KB/s 解释了。"
    )

    answer = answer_deterministically("我是小b", history)

    assert answer is not None
    assert answer.route == "deterministic_speed_unit_correction"
    assert answer.response.startswith("对，按小写 b 计算")
    assert "5.29 Mbps" in answer.response
    assert "662 KB/s" in answer.response
    assert "646 KiB/s" in answer.response


def test_speed_followup_big_b_uses_byte_semantics():
    history = "RECENT CONVERSATION HISTORY:\n  [1] user: 5295KB/s是多少速度"

    answer = answer_deterministically("大B", history)

    assert answer is not None
    assert answer.response.startswith("对，按大写 B 计算")
    assert "42.4 Mbps" in answer.response
    assert "5295 KB/s" in answer.response
    assert "5171 KiB/s" in answer.response


def test_speed_conversion_engine_skips_llm_and_planner():
    llm_calls = []

    def llm_mock(**kwargs):
        llm_calls.append(kwargs)
        return "should not be called"

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
    assert result.final_response.startswith("5295 kb/s 的换算结果")
    assert result.metadata.get("deterministic_answer") is True
    assert result.metadata.get("planner_skipped") is True
    assert result.metadata.get("used_tools") is False
    assert result.metadata.get("llm_calls") == 0
    assert llm_calls == []


def test_speed_correction_engine_uses_history_without_llm():
    llm_calls = []

    def llm_mock(**kwargs):
        llm_calls.append(kwargs)
        return "should not be called"

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
            )
        },
    ))

    assert result.success
    assert result.final_response.startswith("对，按小写 b 计算")
    assert result.metadata.get("deterministic_answer") is True
    assert result.metadata.get("conversation_history_used") is True
    assert result.metadata.get("llm_calls") == 0
    assert llm_calls == []
