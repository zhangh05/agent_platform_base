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


def test_web_manage_weather_batch_reports_exact_partial_coverage(monkeypatch):
    from core.tools.general_tools import web_tools
    from core.tools.schemas import ToolInvocation

    def fake_forecast(inv):
        location = inv.arguments["location"]
        if location == "南京":
            return {"ok": False, "error": "provider unavailable"}
        return {
            "ok": True,
            "resolved_location": {"name": f"{location}, 中国"},
            "forecast_daily": [{"date": "2026-08-21", "condition": "多云"}],
            "summary": f"{location}预报已返回",
        }

    monkeypatch.setattr(web_tools, "handle_weather_forecast", fake_forecast)
    result = web_tools.handle_weather_batch(ToolInvocation(
        tool_id="web.manage",
        arguments={
            "action": "weather_batch", "locations": ["上海", "南京", "杭州"], "days": 10,
        },
        workspace_id="default",
        requested_by="turn_runner",
    ))

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["coverage_status"] == "partial"
    assert result["partial"] is True
    assert result["coverage"]["requested_locations"] == ["上海", "南京", "杭州"]
    assert result["coverage"]["resolved_locations"] == ["上海, 中国", "杭州, 中国"]
    assert result["coverage"]["failed_locations"] == ["南京"]
    assert result["warnings"] == ["partial_location_coverage"]


def test_partial_batch_is_partial_tool_outcome_without_forcing_task_failure():
    from types import SimpleNamespace

    from core.runtime_engine.turn_outcome import (
        derive_execution_outcome,
        derive_tool_execution_outcome,
    )

    results = [SimpleNamespace(
        ok=True,
        execution_may_continue=False,
        output={"partial": True, "coverage_status": "partial"},
    )]

    assert derive_tool_execution_outcome(results) == "partial"
    assert derive_execution_outcome(results) == "complete"


def test_compiled_weather_batch_preserves_scalar_default_as_current(monkeypatch):
    from core.tools.general_tools import web_tools
    from core.tools.schemas import ToolInvocation

    seen = []

    def fake_current(inv):
        seen.append((inv.arguments["location"], inv.arguments["days"]))
        return {
            "ok": True,
            "resolved_location": {"name": inv.arguments["location"]},
            "current": {"condition": "晴"},
        }

    monkeypatch.setattr(web_tools, "handle_weather_current", fake_current)
    result = web_tools.handle_weather_batch(ToolInvocation(
        tool_id="web.manage",
        arguments={"action": "weather_batch", "locations": ["上海", "杭州"]},
        workspace_id="default",
        requested_by="turn_runner",
    ))

    assert result["ok"] is True
    assert sorted(seen) == [("上海", 1), ("杭州", 1)]
    assert [item["requested_location"] for item in result["forecasts"]] == ["上海", "杭州"]


def test_weather_batch_does_not_count_search_fallback_as_structured_success(monkeypatch):
    from core.tools.general_tools import web_tools
    from core.tools.schemas import ToolInvocation

    monkeypatch.setattr(web_tools, "handle_weather_forecast", lambda inv: {
        "ok": True,
        "source_type": "public_web_realtime",
        "results": [{"title": "search result"}],
    })
    result = web_tools.handle_weather_batch(ToolInvocation(
        tool_id="web.manage",
        arguments={"action": "weather_batch", "locations": ["甲地", "乙地"], "days": 2},
        workspace_id="default",
        requested_by="turn_runner",
    ))

    assert result["ok"] is False
    assert result["coverage"]["successful_location_count"] == 0
    assert result["coverage"]["failed_locations"] == ["甲地", "乙地"]


def test_weather_batch_accepts_named_coordinate_locations_through_semantic_gate(monkeypatch):
    from core.runtime_engine.models import ExecutionNode
    from core.runtime_engine.semantic_validator import SemanticValidator
    from core.tools.general_tools import web_tools
    from core.tools.schemas import ToolInvocation

    locations = [
        {"name": "广州", "latitude": 23.11667, "longitude": 113.25},
        {"name": "深圳", "latitude": 22.54554, "longitude": 114.0683},
    ]
    validation = SemanticValidator().validate([ExecutionNode(
        id="coordinate-batch", tool="web.manage",
        args={"action": "weather_batch", "locations": locations, "days": 2},
    )])
    assert validation.valid is True

    observed = []
    def fake_forecast(inv):
        observed.append(dict(inv.arguments))
        return {
            "ok": True,
            "resolved_location": {
                "name": inv.arguments["location"],
                "latitude": inv.arguments["latitude"],
                "longitude": inv.arguments["longitude"],
            },
            "forecast_daily": [{"date": "2026-08-23", "condition": "晴"}],
        }

    monkeypatch.setattr(web_tools, "handle_weather_forecast", fake_forecast)
    result = web_tools.handle_weather_batch(ToolInvocation(
        tool_id="web.manage",
        arguments={"action": "weather_batch", "locations": locations, "days": 2},
        workspace_id="default",
        requested_by="subagent",
    ))

    assert result["ok"] is True
    assert [call["location"] for call in observed] == ["广州", "深圳"]
    assert [(call["latitude"], call["longitude"]) for call in observed] == [
        (23.11667, 113.25), (22.54554, 114.0683),
    ]
    assert result["requested_location_details"] == locations


def test_read_only_timeout_is_failed_not_unknown_execution_outcome():
    from types import SimpleNamespace
    from core.runtime_engine.turn_outcome import (
        derive_execution_outcome,
        derive_tool_execution_outcome,
    )

    result = SimpleNamespace(
        ok=False,
        execution_may_continue=True,
        output={"read_only": True},
    )
    assert derive_tool_execution_outcome([result]) == "failed"
    assert derive_execution_outcome([result], terminal_error="tool_timeout") == "failed"
