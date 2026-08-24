"""QueryLoop must not impose a local semantic response-quality gate."""

from __future__ import annotations

import asyncio
from unittest import mock

from core.runtime_engine import SSOTRuntimeConfig, SSOTRuntimeEngine


def _run(answer: str):
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return answer

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )
    return asyncio.run(engine.run(user_input="回答当前问题", workspace_id="test")), calls


def test_query_loop_does_not_locally_score_or_regenerate_model_answer():
    result, calls = _run("已查询几个主要城市，天气有雷暴伴小冰�。")

    assert result.success is True
    assert "�" in result.final_response
    assert len(calls) == 1
    assert "response_quality_corrections" not in result.metadata
    assert "response_quality_observation" not in result.metadata


def test_query_loop_leaves_semantic_claim_judgment_to_model_and_prompt():
    result, calls = _run("配置已成功部署。")

    assert result.success is True
    assert result.final_response == "配置已成功部署。"
    assert len(calls) == 1
    assert "response_quality_failed" not in result.errors


def test_query_loop_does_not_regex_rewrite_process_style_output():
    result, calls = _run("I have the data. Let me compose a clear summary.")

    assert result.success is True
    assert result.final_response == "I have the data. Let me compose a clear summary."
    assert len(calls) == 1


def test_final_presentation_still_masks_credentials_without_regeneration():
    result, calls = _run("password: super-secret")

    assert result.success is True
    assert result.final_response == "password: [REDACTED]"
    assert len(calls) == 1
