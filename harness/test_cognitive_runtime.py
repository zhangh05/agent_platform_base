import asyncio
from types import SimpleNamespace

from core.runtime_engine.cognitive_events import COGNITIVE_DECISION_MADE, build_cognitive_event
from core.runtime_engine.cognitive_state import initialize_cognitive_state


def test_cognitive_state_is_bounded_versioned_and_projects_only_safe_summary():
    state = initialize_cognitive_state(turn_id="turn-1", trace_id="trace-1", user_input="查询杭州天气", constraints=["使用真实数据"])
    assert state.revision == 2
    state.select_plan([{"action": "weather.forecast", "purpose": "获取天气"}])
    state.add_fact("杭州未来十天数据已获取", source="weather.forecast", evidence_id="call-1")
    state.add_unknown("是否需要用户选择城市", blocking=False)
    state.set_decision("execute_tool", reason_codes=["missing_required_evidence"], selected_action="weather.forecast", visible_summary="需要获取真实天气数据")
    summary = state.summary()
    assert summary["goal"] == "查询杭州天气"
    assert summary["known_fact_count"] == 1
    assert summary["unknown_count"] == 1
    assert summary["decision"]["selected_action"] == "weather.forecast"
    assert "events" not in summary
    assert state.as_trace_payload()["events"]


def test_cognitive_event_discards_untrusted_payload_fields_and_bounds_text():
    event = build_cognitive_event(
        COGNITIVE_DECISION_MADE,
        turn_id="turn-1",
        trace_id="trace-1",
        state_revision=3,
        payload={"decision": "execute_tool", "reason_codes": ["gap"], "raw_reasoning": "must never persist", "visible_summary": "x" * 1000},
    )
    assert event["payload"]["decision"] == "execute_tool"
    assert "raw_reasoning" not in event["payload"]
    assert len(event["payload"]["visible_summary"]) == 320


def test_query_loop_honors_cancel_arriving_during_final_llm_response():
    from agent.llm.schemas import LLMResponse
    from core.runtime_engine.budget_controller import BudgetController
    from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.query_loop import QueryLoop

    cancelled = {"value": False}

    def llm(**_kwargs):
        cancelled["value"] = True
        return LLMResponse(content="这条生成中的答复不应在用户取消后提交。")

    config = SSOTRuntimeConfig(max_query_loop_iterations=1)
    loop = QueryLoop(config, {}, object(), llm_invoke=llm)
    context = StatelessContext(
        workspace_id="default", session_id="session-cancel-race",
        request_id="request-cancel-race", user_input="生成答复",
        extras={"cancel_check": lambda: cancelled["value"]},
    )
    result = asyncio.run(loop.run(context, BudgetController(config), None))
    assert result.error == "cancelled_by_user"
    assert result.final_response == "任务已取消。"


def test_query_loop_projects_server_generated_cognitive_events_once():
    from agent.llm.schemas import LLMResponse
    from core.runtime_engine.budget_controller import BudgetController
    from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.query_loop import QueryLoop

    class CaptureEmitter:
        def __init__(self):
            self.calls = []

        def emit(self, name, payload):
            self.calls.append((name, payload))

    config = SSOTRuntimeConfig(max_query_loop_iterations=1)
    emitter = CaptureEmitter()
    loop = QueryLoop(
        config,
        {},
        object(),
        llm_invoke=lambda **_kwargs: LLMResponse(content="杭州天气适宜出行。"),
        emitter=emitter,
    )
    context = StatelessContext(
        workspace_id="default",
        session_id="session-cognitive",
        request_id="request-cognitive",
        user_input="杭州天气如何？",
        extras={"cognitive": {"outcome": "forged"}},
    )

    result = asyncio.run(loop.run(context, BudgetController(config), None))

    summary = result.metrics["cognitive"]
    assert summary["outcome"] == "model_directed"
    assert summary["goal"] == "杭州天气如何？"
    assert summary["outcome"] != context.extras["cognitive"]["outcome"]
    event_ids = [event["event_id"] for event in result.metrics["cognitive_events"]]
    emitted = [payload for name, payload in emitter.calls if name.startswith("cognitive_")]
    assert len(event_ids) == len(set(event_ids))
    assert [event["event_id"] for event in emitted] == event_ids


def test_agent_result_contract_normalizes_cognitive_projection_and_labels():
    from backend.core.agent_contract import normalize_agent_result
    from core.runtime_engine.stage_events import label_for

    normalized = normalize_agent_result(
        {"metadata": {"cognitive": {"outcome": "stop_completed"}}},
        "workspace-cognitive",
    )

    assert normalized["cognitive"] == {"outcome": "stop_completed"}
    assert normalized["metadata"]["cognitive_events"] == []
    assert normalized["cognitive_events"] == []
    assert label_for("cognitive_model_state_recorded") == "模型决策状态已记录"

def test_cognitive_state_marks_conflicting_claims_as_blocking_unknown():
    from types import SimpleNamespace
    from core.runtime_engine.cognitive_state import initialize_cognitive_state

    state = initialize_cognitive_state(turn_id="t-conflict", trace_id="x-conflict", user_input="核对设备状态")
    state.register_tool_results([SimpleNamespace(
        tool_name="probe", call_id="call-a", ok=True,
        output={"fact_key": "device.status", "summary": "设备状态为 UP"},
        summary="设备状态为 UP",
    )])
    state.register_tool_results([SimpleNamespace(
        tool_name="probe", call_id="call-b", ok=True,
        output={"fact_key": "device.status", "summary": "设备状态为 DOWN"},
        summary="设备状态为 DOWN",
    )])

    assert state.summary()["known_fact_count"] == 0
    assert state.summary()["conflict_count"] == 1
    assert state.summary()["blocking_unknown_count"] == 1
    assert state.unknowns[-1]["reason"] == "evidence_conflict"


def test_cognitive_state_clears_transient_failure_when_same_step_recovers():
    state = initialize_cognitive_state(
        turn_id="t-replan", trace_id="x-replan", user_input="分析文件",
    )
    failed = SimpleNamespace(
        tool_name="workspace.file", call_id="provider-a", ok=False,
        output={"_orchestration": {"step_id": "extract"}, "error": "temporary failure"},
        error="temporary failure", execution_may_continue=False,
    )
    recovered = SimpleNamespace(
        tool_name="workspace.file", call_id="provider-b", ok=True,
        output={"_orchestration": {"step_id": "extract"}, "summary": "document extracted"},
        summary="document extracted", execution_may_continue=False,
    )

    state.register_tool_results([failed])
    assert state.summary()["unknown_count"] == 1
    state.register_tool_results([recovered])
    assert state.summary()["unknown_count"] == 0
    assert state.summary()["known_fact_count"] == 1
