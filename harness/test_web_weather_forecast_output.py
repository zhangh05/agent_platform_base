"""Weather tool should expose the full forecast payload to the LLM."""

from __future__ import annotations


def test_web_manage_weather_preserves_multi_day_forecast(monkeypatch):
    import core.tools.canonical_registry as cr
    from core.tools.schemas import ToolInvocation

    def fake_forecast(inv):
        return {
            "ok": True,
            "status": "ok",
            "tool_id": "web.weather.forecast",
            "summary": "杭州 10 天预报已返回",
            "forecast_daily": [{"date": f"2026-07-{i:02d}", "condition": "多云"} for i in range(1, 11)],
            "count": 10,
            "results_markdown": "10 day markdown",
            "answer_hint": "Use all forecast_daily rows.",
        }

    monkeypatch.setattr(cr, "handle_weather_forecast", fake_forecast)

    result = cr._weather_merged(ToolInvocation(
        tool_id="web.manage",
        arguments={"action": "weather", "location": "杭州", "days": 10},
        workspace_id="default",
        requested_by="turn_runner",
    ))

    assert result["ok"] is True
    assert result["output"]["count"] == 10
    assert len(result["output"]["forecast_daily"]) == 10
    assert result["output"]["answer_hint"] == "Use all forecast_daily rows."


def test_weather_result_uses_natural_chinese_labels_and_coverage():
    from core.tools.general_tools.shared_web import (
        _weather_code_label,
        _weather_structured_result,
    )

    result = _weather_structured_result(
        tool_id="web.weather.forecast",
        location="杭州",
        units="metric",
        language="zh-CN",
        structured={
            "ok": True,
            "resolved_location": {"name": "杭州, 浙江, 中国"},
            "forecast_daily": [{"date": "2026-08-11", "condition": "阵雨"}],
        },
    )

    assert _weather_code_label(53) == "毛毛雨"
    assert _weather_code_label(81) == "阵雨"
    assert _weather_code_label(96) == "雷暴，可能伴少量冰雹"
    assert result["coverage"] == {
        "requested_locations": ["杭州"],
        "resolved_locations": ["杭州, 浙江, 中国"],
        "location_count": 1,
        "forecast_days": 1,
    }
    assert "最多 7 列" in result["answer_hint"]
