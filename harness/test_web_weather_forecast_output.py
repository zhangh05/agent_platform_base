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
