"""Regression coverage for LLM-owned network CLI recovery."""
from __future__ import annotations

import asyncio

from agent.llm.schemas import LLMResponse, LLMToolCall
from core.runtime_engine.engine import SSOTRuntimeEngine
from core.runtime_engine.models import SSOTRuntimeConfig
from core.runtime_engine.tool_runtime import ToolRuntime
from extensions.network_operations.read_recovery import (
    infer_semantic_fact,
    model_recovery_guidance,
    network_evidence_claims,
    semantic_collect_guidance,
)


def _rejected_bgp_output() -> dict:
    return {
        "ok": False,
        "read_ok": False,
        "command_results": [{
            "command": "display bgp peer vpn4",
            "error_code": "device_command_rejected",
            "device_error": "% Unrecognized command",
            "complete": True,
        }],
        "device_profile": {
            "driver_id": "h3c.comware",
            "vendor": "h3c",
            "semantic_facts": ["bgp_peers", "device_version"],
        },
    }


def test_rejected_read_returns_advisory_context_without_executable_fallback():
    guidance = model_recovery_guidance(
        {"action": "read", "connection_id": "conn-1", "commands": ["display bgp peer vpn4"]},
        _rejected_bgp_output(),
    )

    assert guidance[0]["candidate_semantic_fact"] == "bgp_peers"
    assert guidance[0]["decision_owner"] == "llm"
    assert "tool_id" not in guidance[0]
    assert "arguments" not in guidance[0]
    assert infer_semantic_fact("display bgp peer vpn4") == "bgp_peers"


def test_unknown_syntax_exposes_search_hint_but_does_not_schedule_search():
    output = _rejected_bgp_output()
    output["command_results"][0]["command"] = "display proprietary foo status"
    guidance = model_recovery_guidance(
        {"action": "read", "connection_id": "conn-1", "commands": ["display proprietary foo status"]},
        output,
    )

    assert guidance[0]["candidate_semantic_fact"] == ""
    assert "proprietary foo" in guidance[0]["documentation_query_hint"]
    assert guidance[0]["allowed_next_steps"][-1] == "report_unknown"


def test_transport_uncertainty_and_writes_never_publish_semantic_recovery():
    output = _rejected_bgp_output()
    output["execution_may_continue"] = True
    assert model_recovery_guidance(
        {"action": "read", "connection_id": "conn-1", "commands": ["display bgp peer vpn4"]}, output,
    ) == []
    assert model_recovery_guidance(
        {"action": "configure", "connection_id": "conn-1", "commands": ["bgp 1"]}, _rejected_bgp_output(),
    ) == []


def test_unavailable_semantic_template_returns_model_guidance_only():
    guidance = semantic_collect_guidance(
        {"action": "collect", "connection_id": "conn-1", "facts": ["bgp_peers"]},
        {"facts": {"bgp_peers": {"status": "unavailable"}}, "device_profile": {"vendor": "h3c"}},
    )

    assert guidance[0]["reason"] == "semantic_template_unavailable"
    assert guidance[0]["decision_owner"] == "llm"
    assert "tool_id" not in guidance[0]


def test_query_loop_runs_only_the_recovery_action_selected_by_the_llm():
    responses = [
        LLMResponse(tool_calls=[LLMToolCall(
            id="wrong-command", name="network.operations.device.manage",
            arguments={"action": "read", "connection_id": "conn-1", "commands": ["display bgp peer vpn4"]},
        )]),
        LLMResponse(tool_calls=[LLMToolCall(
            id="chosen-collect", name="network.operations.device.manage",
            arguments={"action": "collect", "connection_id": "conn-1", "facts": ["bgp_peers"]},
        )]),
        LLMResponse(content="已根据两轮设备反馈完成判断。"),
    ]
    received: list[dict] = []

    def llm(**_kwargs):
        return responses.pop(0)

    def device_manage(arguments: dict):
        received.append(dict(arguments))
        if arguments["action"] == "read":
            output = _rejected_bgp_output()
            output["ok"] = True
            output["connection_ok"] = True
            output["model_recovery_guidance"] = model_recovery_guidance(arguments, output)
            return output
        output = {"ok": True, "facts": {"bgp_peers": {"status": "collected", "observation_status": "observed"}}}
        output["evidence_claims"] = network_evidence_claims(arguments, output)
        return output

    registry = {"network.operations.device.manage": {
        "description": "network device operation",
        "args_schema": {"type": "object", "required": ["action", "connection_id"], "properties": {
            "action": {"type": "string", "enum": ["read", "collect", "configure", "probe"]},
            "connection_id": {"type": "string"}, "commands": {"type": "array"}, "facts": {"type": "array"},
        }},
    }}
    runtime = ToolRuntime(SSOTRuntimeConfig(max_query_loop_iterations=4))
    runtime.register("network.operations.device.manage", device_manage)
    outcome = asyncio.run(SSOTRuntimeEngine(
        SSOTRuntimeConfig(max_query_loop_iterations=4), llm_invoke=llm,
        tool_registry=registry, tool_runtime=runtime,
    ).run("查看设备 BGP 邻居", workspace_id="test", session_id="session"))

    assert [item["action"] for item in received] == ["read", "collect"]
    assert outcome.success is True
    assert not outcome.metadata.get("safe_read_recovery_events")
