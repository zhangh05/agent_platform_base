from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock

from core.runtime_engine import SSOTRuntimeConfig, SSOTRuntimeEngine
from core.runtime_engine.response_quality import validate_response_quality


def test_quality_gate_detects_corrupt_scope_and_wide_table():
    draft = (
        "已查询几个主要城市，杭州雷暴伴小冰�。\n\n"
        "| 城市 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 杭州 | 晴 | 晴 | 晴 | 晴 | 晴 | 晴 | 晴 |"
    )

    issues = validate_response_quality(draft, user_input="全部")

    assert {issue.code for issue in issues} == {
        "CORRUPT_UNICODE",
        "SCOPE_SILENTLY_NARROWED",
        "TABLE_TOO_WIDE",
    }


def test_explicit_partial_scope_is_honest_and_accepted():
    issues = validate_response_quality(
        "本次查询范围仅覆盖上海、南京四个主要城市，并非全部城市。",
        user_input="全部",
    )
    assert issues == []


def test_weather_evidence_rejects_literal_or_wrong_domain_wording():
    issues = validate_response_quality(
        "## 出行与防务提示\n明天为中等毛毛雨。",
        user_input="明天天气",
        tool_results=[SimpleNamespace(
            tool_name="web.manage",
            call_id="weather-1",
            ok=True,
            output={"source_type": "structured_weather"},
        )],
    )

    assert [issue.code for issue in issues] == ["UNNATURAL_WEATHER_TERMINOLOGY"]


def test_quality_gate_rejects_unverified_action_completion():
    issues = validate_response_quality("配置已成功部署。", user_input="部署配置")

    assert [issue.code for issue in issues] == ["UNVERIFIED_ACTION_COMPLETION"]


def test_quality_gate_accepts_action_claim_with_successful_tool_evidence():
    issues = validate_response_quality(
        "配置已成功部署。",
        user_input="部署配置",
        tool_results=[SimpleNamespace(ok=True, output={"status": "complete"})],
    )

    assert issues == []


def test_quality_gate_rejects_credential_assignment():
    issues = validate_response_quality("password: super-secret", user_input="显示密码")

    assert [issue.code for issue in issues] == ["SENSITIVE_OUTPUT"]


def test_quality_gate_rejects_made_up_runtime_reference():
    issues = validate_response_quality(
        "报告已生成：report_deadbeef99",
        user_input="生成报告",
        tool_results=[SimpleNamespace(ok=True, output={"status": "complete"})],
    )

    assert [issue.code for issue in issues] == ["UNVERIFIED_REFERENCE"]


def test_quality_gate_accepts_reference_returned_by_tool():
    issues = validate_response_quality(
        "报告已生成：report_deadbeef99",
        user_input="生成报告",
        tool_results=[SimpleNamespace(
            ok=True,
            output={"status": "complete", "report_id": "report_deadbeef99"},
        )],
    )

    assert issues == []


def test_query_loop_observes_corrupt_text_without_replacing_final_answer():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "已查询几个主要城市，天气有雷暴伴小冰�。"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(user_input="全部", workspace_id="test"))

    assert result.success
    assert "�" in result.final_response
    assert len(calls) == 1
    observation = result.metadata["response_quality_observation"]
    assert "CORRUPT_UNICODE" in observation["codes"]
    assert observation["correction_attempts"] == 0
    assert observation["blocking"] is False


def test_query_loop_corrects_unverified_claim_before_delivery():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return "配置已成功部署。"
        return "尚未执行部署，无法确认成功。"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(user_input="部署配置", workspace_id="test"))

    assert result.success is True
    assert "response_quality_failed" not in result.errors
    assert result.final_response == "尚未执行部署，无法确认成功。"
    assert len(calls) == 2
    assert result.metadata["response_quality_corrections"] == 1


def test_query_loop_stops_if_unverified_claim_persists():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "配置已成功部署。"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(user_input="部署配置", workspace_id="test"))

    assert result.success is False
    assert "response_quality_failed" in result.errors
    assert "配置已成功部署" not in result.final_response
    assert len(calls) == 2
