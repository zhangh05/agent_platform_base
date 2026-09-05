import asyncio
import json

from agent.llm.schemas import LLMResponse, LLMToolCall
from core.runtime_engine.budget_controller import BudgetController
from core.runtime_engine.evidence import register_tool_evidence
from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
from core.runtime_engine.query_loop import (
    FINAL_SYNTHESIS_CHECKPOINT_MARKER,
    QueryLoop,
    StreamingToolResult,
    _json_compact,
    _model_tool_payload,
)
from core.runtime_engine.tool_runtime import ToolRuntime


def _six_device_results() -> list[StreamingToolResult]:
    return [
        StreamingToolResult(
            tool_name="network.operations.device.manage",
            call_id=f"device-call-{index}",
            ok=True,
            output={
                "status": "succeeded",
                "device_id": f"device-{index}",
                "facts": {"current_config": {"status": "collected"}},
                "output": {
                    "display current-configuration": (
                        f"sysname PE{index}\n"
                        "mpls lsr-id 10.0.0.1\n"
                        "bgp 65000\n"
                        "peer 10.0.0.2 as-number 65001\n"
                        "ipv4-family labeled-unicast\n"
                    ),
                },
            },
        )
        for index in range(6)
    ]


def test_final_synthesis_recovery_is_tool_free_and_uses_all_evidence():
    captured = {}

    def llm(**kwargs):
        captured.update(kwargs)
        return LLMResponse(content="六台设备的 Option C 配置已完成综合分析。")

    config = SSOTRuntimeConfig(max_llm_calls=4)
    loop = QueryLoop(config, {}, object(), llm_invoke=llm)
    context = StatelessContext(
        workspace_id="default",
        session_id="session-six",
        request_id="run-six",
        user_input="分析六台设备的 MPLS VPN option C 配置",
        extras={},
    )
    results = _six_device_results()
    register_tool_evidence(context.extras, results, user_input=context.user_input)

    answer = asyncio.run(loop._recover_final_synthesis(
        context,
        BudgetController(config),
    ))

    assert "六台设备" in answer
    assert captured["tools"] == []
    body = str(captured["messages"][-1].content)
    assert body.count('"evidence_id"') == 6
    assert "device-5" in body
    assert context.extras["synthesis_recovery"]["ok"] is True


def test_max_iteration_exit_reserves_a_tool_free_final_synthesis():
    """Planning exhaustion must not replace completed evidence with a ledger."""
    responses = [
        LLMResponse(tool_calls=[
            LLMToolCall(
                id="read-once",
                name="data.manage",
                arguments={"action": "parse", "text": "device inventory"},
            ),
        ]),
        LLMResponse(content="已采集设备清单；未发现其他已核验结论。"),
    ]
    calls = []

    def llm(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    config = SSOTRuntimeConfig(max_query_loop_iterations=1, max_llm_calls=3)
    runtime = ToolRuntime(config)
    runtime.register("data.manage", lambda _args: {"ok": True, "rows": [{"device": "PE1"}]})
    registry = {
        "data.manage": {
            "description": "read data",
            "args_schema": {"type": "object", "properties": {"action": {"type": "string"}}},
        },
    }
    loop = QueryLoop(config, registry, runtime, llm_invoke=llm)
    context = StatelessContext(
        workspace_id="default", session_id="max-iteration", request_id="max-iteration",
        user_input="了解设备", extras={},
    )

    result = asyncio.run(loop.run(context, BudgetController(config), None))

    assert result.final_response == "已采集设备清单；未发现其他已核验结论。"
    assert result.error is None
    assert result.metrics["planning_checkpoint_reached"] is True
    assert len(calls) == 2
    assert calls[-1]["tools"] == []
    assert context.extras["synthesis_recovery"]["ok"] is True


def test_final_synthesis_retries_without_repeating_tool_work():
    responses = [
        LLMResponse(error="provider_temporarily_unavailable"),
        LLMResponse(content="已基于已采集的设备证据完成分析。"),
    ]
    calls = []

    def llm(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    config = SSOTRuntimeConfig(max_llm_calls=3)
    loop = QueryLoop(config, {}, object(), llm_invoke=llm)
    context = StatelessContext(
        workspace_id="default", session_id="retry-synthesis", request_id="retry-synthesis",
        user_input="分析设备", extras={},
    )
    register_tool_evidence(context.extras, _six_device_results(), user_input=context.user_input)

    answer = asyncio.run(loop._recover_final_synthesis(context, BudgetController(config)))

    assert answer == "已基于已采集的设备证据完成分析。"
    assert len(calls) == 2
    assert all(call["tools"] == [] for call in calls)
    assert context.extras["synthesis_recovery"]["attempts"] == 2
    assert context.extras["synthesis_recovery"]["attempt_errors"] == [
        "llm_provider_error"
    ]


def test_deterministic_fallback_lists_evidence_without_raw_transcript():
    config = SSOTRuntimeConfig()
    loop = QueryLoop(config, {}, object())
    context = StatelessContext(
        workspace_id="default",
        session_id="session-six",
        request_id="run-six",
        user_input="分析配置",
        extras={},
    )
    results = _six_device_results()
    register_tool_evidence(context.extras, results, user_input=context.user_input)

    answer = loop._build_tool_result_fallback(context, results)

    assert "证据采集已完成" in answer
    assert "device-0" in answer
    assert "display current-configuration" not in answer
    assert answer.count("证据：") == 6


def test_provider_empty_response_retries_once_then_reports_typed_error(monkeypatch):
    from agent.llm.runtime import _generate_with_retry
    from agent.llm.schemas import LLMMessage, LLMRequest

    attempts = []

    def generate(_request, _config):
        attempts.append(1)
        return LLMResponse(content="", finish_reason="stop", provider="mock", model="mock")

    monkeypatch.setattr("agent.llm.provider.generate", generate)
    response = _generate_with_retry(
        LLMRequest(task="assistant_chat", messages=[LLMMessage(role="user", content="hello")]),
        {"provider": "mock", "model": "mock"},
    )

    assert len(attempts) == 2
    assert response.error == "provider_empty_response"
    assert response.metadata["finish_reason"] == "stop"


def test_terminal_tracking_contract_requests_final_synthesis():
    result = StreamingToolResult(
        tool_name="network.operations.inspection",
        call_id="poll-terminal",
        ok=True,
        output={
            "coverage_status": "partial",
            "tracking": {
                "kind": "long_task",
                "task_id": "inspection-1",
                "status": "partial",
                "done": True,
                "suggested_next_action": "synthesize_results",
            },
        },
    )

    assert QueryLoop._producer_requests_final_synthesis([result]) is True
    assert QueryLoop._has_final_synthesis_checkpoint([
        type("Message", (), {"role": "user", "content": FINAL_SYNTHESIS_CHECKPOINT_MARKER})(),
    ]) is True


def test_tool_message_hard_cap_preserves_all_multi_device_members():
    payload = {
        "ok": True,
        "analysis_projection": {
            "coverage": {"total": 6, "succeeded": 6, "failed": 0},
            "devices": [
                {
                    "name": f"DEVICE_{index}",
                    "status": "succeeded",
                    "current_config": {
                        "signals": {
                            "identity": {"router_id": f"{index}.{index}.{index}.{index}"},
                            "neighbors": [
                                {"peer": f"10.0.{index}.{peer}", "remote_as": 65000 + peer}
                                for peer in range(20)
                            ],
                            "padding": "x" * 4000,
                        },
                    },
                }
                for index in range(6)
            ],
        },
    }

    rendered = _json_compact(payload, max_chars=6000)

    assert len(rendered) <= 6000
    decoded = json.loads(rendered)
    device_names = [item.get("name") for item in decoded["analysis_projection"]["devices"]]
    assert device_names == [f"DEVICE_{index}" for index in range(6)]
    assert decoded["_projection"]["strategy"] == "structure_preserving_fair_share"


def test_tool_round_prefers_synthesis_projection_over_full_long_task_record():
    loop = QueryLoop(SSOTRuntimeConfig(), {}, None)
    messages = loop._append_tool_round(
        [],
        [LLMToolCall(id="inspection-call", name="network.operations.inspection", arguments={})],
        [StreamingToolResult(
            tool_name="network.operations.inspection",
            call_id="inspection-call",
            ok=True,
            output={
                "status": "succeeded",
                "_evidence_projection": {
                    "coverage_status": "complete",
                    "tracking": {"done": True, "suggested_next_action": "synthesize_results"},
                    "analysis_projection": {
                        "coverage": {"total": 6, "succeeded": 6},
                        "devices": [{"name": f"DEVICE_{index}"} for index in range(6)],
                    },
                    "task": {"large_internal_record": "must-not-reach-model"},
                },
            },
        )],
    )

    content = str(messages[-1].content)
    assert "analysis_projection" in content
    assert "DEVICE_5" in content
    assert "must-not-reach-model" not in content


def test_auto_tracking_preserves_every_target_and_literal_evidence():
    loop = QueryLoop(SSOTRuntimeConfig(), {}, None)
    devices = [
        {
            "name": f"DEVICE_{index}",
            "current_config": {"signals": {
                "interfaces": [f"[interface GE0/1] ip address 10.{index}.0.1 255.255.255.0"],
                "vpn": ["vpn-target 3:3 export-extcommunity"],
            }},
            "fact_evidence": {"bgp": {"observations": [{
                "literal_excerpt": "Established\n" + "route row\n" * 1200 + "ISIS is not configured",
            }]}},
        }
        for index in range(6)
    ]
    result = StreamingToolResult(
        tool_name="network.operations.inspection", call_id="internal-poll", ok=True,
        output={
            "analysis_projection": {"devices": devices},
            "task": {"duplicate_raw_output": "DO_NOT_DUPLICATE" * 10000},
        },
    )
    messages = loop._append_tool_round([], [], [result])
    content = str(messages[-1].content)
    assert messages[-1].role == "user"
    for index in range(6):
        assert f"DEVICE_{index}" in content
        assert f"10.{index}.0.1" in content
    assert content.count("Established") == 6
    assert content.count("ISIS is not configured") == 6
    assert "vpn-target 3:3 export-extcommunity" in content
    assert "DO_NOT_DUPLICATE" not in content
    assert len(content) < 37000


def test_complete_artifact_is_not_replaced_by_bounded_projection():
    result = StreamingToolResult(
        tool_name="workspace.artifact", call_id="artifact", ok=True,
        output={"content_complete": True, "artifact_type": "report",
                "content": "complete evidence", "_evidence_projection": {"content": "preview"}},
    )
    assert _model_tool_payload(result)["content"] == "complete evidence"


def test_error_survives_empty_producer_error_list():
    result = StreamingToolResult(
        tool_name="tool", call_id="error", ok=False, error="connection_failed",
        output={"errors": []},
    )
    assert "connection_failed" in _model_tool_payload(result)["errors"]


def test_synthesis_recovery_respects_cancellation_before_and_during_generation():
    for cancel_before in (True, False):
        cancelled = [cancel_before]
        calls = []

        def llm(**_kwargs):
            calls.append(True)
            cancelled[0] = True
            return LLMResponse(content="late response must be discarded")

        config = SSOTRuntimeConfig()
        loop = QueryLoop(config, {}, object(), llm_invoke=llm)
        context = StatelessContext(
            workspace_id="default", session_id="cancel", request_id="cancel",
            user_input="analyse", extras={"cancel_check": lambda: cancelled[0]},
        )
        answer = asyncio.run(loop._recover_final_synthesis(context, BudgetController(config)))
        assert answer == ""
        assert len(calls) == (0 if cancel_before else 1)
        assert context.extras["synthesis_recovery"]["error"] == "cancelled_by_user"


def test_transport_adapter_preserves_report_containing_completion_phrases():
    from types import SimpleNamespace
    from agent.runtime.ssot_runtime import _final_response

    report = "核查已完成。\n\n六台设备工具执行成功，但数据面未验证。\n接口及邻居如下……"
    assert _final_response(SimpleNamespace(final_response=report)) == report
    assert _final_response(SimpleNamespace(final_response="好")) == "好"
    assert _final_response(SimpleNamespace(final_response="<think>private</think>\n" + report)) == report
