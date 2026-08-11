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


def test_query_loop_corrects_rejected_final_answer_before_returning():
    calls: list[dict] = []
    answers = iter([
        "已查询几个主要城市，天气有雷暴伴小冰�。",
        "本次查询范围仅覆盖上海、南京，并非全部城市；如需完整范围需先明确城市清单。",
    ])

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return next(answers)

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(user_input="全部", workspace_id="test"))

    assert result.success
    assert "并非全部城市" in result.final_response
    assert "�" not in result.final_response
    assert len(calls) == 2
    assert "RUNTIME RESPONSE QUALITY CORRECTION" in calls[1].get("user", "")


def test_query_loop_never_persists_bad_text_after_correction_budget():
    calls: list[dict] = []

    def llm_mock(**kwargs):
        calls.append(kwargs)
        return "仍然是损坏字符�"

    engine = SSOTRuntimeEngine(
        config=SSOTRuntimeConfig(),
        llm_invoke=llm_mock,
        tool_runtime=mock.MagicMock(),
    )

    result = asyncio.run(engine.run(user_input="解释结果", workspace_id="test"))

    assert result.success is False
    assert "response_quality_failed" in result.errors
    assert "�" not in result.final_response
    assert len(calls) == 3
