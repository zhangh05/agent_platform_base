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


def test_quality_gate_rejects_process_only_transition_after_tool_results():
    issues = validate_response_quality(
        "I have all the weather data. Let me compose a clear summary.",
        user_input="查看未来十天珠三角城市天气",
        tool_results=[SimpleNamespace(ok=True, output={"source_type": "structured_weather"})],
    )

    assert {issue.code for issue in issues} == {
        "PROCESS_ONLY_RESPONSE",
        "USER_LANGUAGE_MISMATCH",
    }


def test_quality_gate_does_not_reject_complete_english_answer_for_english_user():
    issues = validate_response_quality(
        "The forecast is warm and humid, with thunderstorms likely tomorrow.",
        user_input="What is the weather forecast?",
        tool_results=[SimpleNamespace(ok=True, output={"source_type": "structured_weather"})],
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


def test_query_loop_corrects_process_only_transition_before_delivery():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            from agent.llm.provider import LLMResponse
            from agent.llm.schemas import LLMToolCall
            return LLMResponse(
                content="",
                tool_calls=[LLMToolCall(
                    id="weather-1",
                    name="web.manage",
                    arguments={"action": "weather", "location": "广州"},
                )],
            )
        if len(calls) == 2:
            return "I have all the weather data. Let me compose a clear summary."
        return "广州未来十天以高温湿热为主，期间有雷阵雨，请关注临近预报。"

    class Runtime:
        @staticmethod
        def has_tool(_name):
            return True

        @staticmethod
        def invoke_raw(_tool_id, _arguments):
            return {
                "ok": True,
                "source_type": "structured_weather",
                "summary": "广州未来十天有雷阵雨",
            }

    runtime = Runtime()
    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(max_query_loop_iterations=5),
        llm_invoke=llm_mock,
        tool_runtime=runtime,
    )

    result = asyncio.run(engine.run(user_input="查看广州未来十天天气", workspace_id="test"))

    assert result.success is True
    assert result.final_response.startswith("广州未来十天")
    assert result.metadata["response_quality_corrections"] == 1
    assert len(calls) == 3


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


def test_quality_gate_allows_textual_scope_deletion_without_tool_evidence():
    contract = {"relation": {"kind": "scope"}}
    issues = validate_response_quality(
        "已删除其他章节，仅保留以下三条文本。\nPARK-01：检查项一。\nPARK-02：检查项二。\nPARK-03：检查项三。",
        user_input="删除其他章节，只保留3条。",
        task_continuation_contract=contract,
    )
    assert issues == []


def test_quality_gate_keeps_external_action_claim_blocking_for_scope_contract():
    contract = {"relation": {"kind": "scope"}}
    issues = validate_response_quality(
        "已上传交接文档。",
        user_input="删除其他章节，只保留3条。",
        task_continuation_contract=contract,
    )
    assert [issue.code for issue in issues] == ["UNVERIFIED_ACTION_COMPLETION"]


def test_quality_gate_keeps_textual_deletion_blocking_without_contract():
    issues = validate_response_quality(
        "已删除生产配置。",
        user_input="删除生产配置。",
    )
    assert [issue.code for issue in issues] == ["UNVERIFIED_ACTION_COMPLETION"]


def test_quality_gate_accepts_textual_scope_deletion_with_exact_delivery_contract():
    contract = {
        "relation": {"kind": "scope"},
        "validation": {
            "kind": "enumerated_items",
            "mode": "replace_scope",
            "expected_total_items": 3,
            "expected_start_ordinal": 1,
            "required_prefix": "PARK-",
            "unit": "条",
        },
    }
    issues = validate_response_quality(
        "已删除其他章节，仅保留以下三条文本。\nPARK-01：检查项一。\nPARK-02：检查项二。\nPARK-03：检查项三。",
        user_input="删除其他章节，只保留3条，并保持 PARK- 前缀和连续编号。",
        task_continuation_contract=contract,
    )
    assert issues == []


def test_shared_enumerated_parser_accepts_space_delimiter_and_inline_markers():
    from core.runtime_engine.enumerated_items import extract_enumerated_items

    items = extract_enumerated_items(
        "PARK-01 检查核心设备。PARK-02 检查汇聚设备。\n- PARK-03：检查接入设备。"
    )
    assert [(item.prefix, item.ordinal) for item in items] == [
        ("PARK-", 1),
        ("PARK-", 2),
        ("PARK-", 3),
    ]


def test_quality_gate_accepts_space_delimited_scope_contract_output():
    contract = {
        "relation": {"kind": "scope"},
        "validation": {
            "kind": "enumerated_items",
            "mode": "replace_scope",
            "expected_total_items": 3,
            "expected_start_ordinal": 1,
            "required_prefix": "PARK-",
            "unit": "条",
        },
    }
    issues = validate_response_quality(
        "PARK-01 检查核心设备。PARK-02 检查汇聚设备。\nPARK-03 检查接入设备。",
        user_input="删除其他章节，只保留3条，并保持 PARK- 前缀和连续编号。",
        task_continuation_contract=contract,
    )
    assert issues == []


def test_quality_gate_rejects_explicit_per_city_daily_weather_delivery_when_truncated():
    from types import SimpleNamespace

    from core.runtime_engine.response_quality import validate_response_quality

    issues = validate_response_quality(
        "已完成查询。南通的逐日明细因响应体较大被截断；扬州为（批量已获取）。",
        user_input="每个城市都必须使用独立调用，并逐日返回未来十天天气。",
        tool_results=[SimpleNamespace(ok=True, output={"source_type": "structured_weather"})],
    )

    assert [issue.code for issue in issues] == ["EXPLICIT_WEATHER_DELIVERY_INCOMPLETE"]


def test_quality_gate_rejects_explicit_weather_daily_answer_without_all_dated_rows():
    from types import SimpleNamespace

    from core.runtime_engine.response_quality import validate_response_quality

    issues = validate_response_quality(
        "已完成。| 城市 | 日期 | 天气 |\n| --- | --- | --- |\n| 上海 | 8/23 | 晴 |",
        user_input="必须覆盖以下 2 个城市：上海、南京。每个城市必须逐日返回未来 2 天的天气。",
        tool_results=[SimpleNamespace(ok=True, output={"source_type": "structured_weather"})],
    )

    assert [issue.code for issue in issues] == ["EXPLICIT_WEATHER_DAILY_COVERAGE_INCOMPLETE"]
