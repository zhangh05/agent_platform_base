"""Regression coverage for server-owned network CLI read recovery."""
from __future__ import annotations

import asyncio

from agent.llm.schemas import LLMResponse, LLMToolCall
from core.runtime_engine.engine import SSOTRuntimeEngine
from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
from core.runtime_engine.query_loop import StreamingToolResult
from core.runtime_engine.tool_runtime import ToolRuntime
from extensions.network_operations.read_recovery import (
    infer_semantic_fact,
    plan_rejected_read_recoveries,
    safe_read_recovery_directive,
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
            "semantic_facts": ["bgp_peers", "device_version"],
        },
    }


def test_rejected_raw_read_maps_to_one_vendor_semantic_fact():
    call = LLMToolCall(
        id="wrong-bgp-command",
        name="network.operations.device.manage",
        arguments={"action": "read", "connection_id": "conn-1", "commands": ["display bgp peer vpn4"]},
    )
    result = type("Result", (), {"output": _rejected_bgp_output(), "execution_may_continue": False})()

    plans = plan_rejected_read_recoveries([call], [result])

    assert len(plans) == 1
    assert plans[0].tool_arguments() == {
        "action": "collect", "connection_id": "conn-1", "facts": ["bgp_peers"],
    }
    assert infer_semantic_fact("display bgp peer vpn4") == "bgp_peers"


def test_recovery_never_applies_to_writes_or_transport_uncertainty():
    write = LLMToolCall(
        id="write", name="network.operations.device.manage",
        arguments={"action": "configure", "connection_id": "conn-1", "commands": ["bgp 1"]},
    )
    read = LLMToolCall(
        id="uncertain", name="network.operations.device.manage",
        arguments={"action": "read", "connection_id": "conn-1", "commands": ["display bgp peer vpn4"]},
    )
    rejected = type("Result", (), {"output": _rejected_bgp_output(), "execution_may_continue": True})()

    assert plan_rejected_read_recoveries([write, read], [rejected, rejected]) == []


def test_unknown_cli_intent_automatically_uses_official_documentation_fallback():
    output = _rejected_bgp_output()
    output["command_results"][0]["command"] = "display proprietary foo status"
    directive = safe_read_recovery_directive(
        {"action": "read", "connection_id": "conn-1", "commands": ["display proprietary foo status"]},
        output,
    )

    assert directive is not None
    assert directive["kind"] == "documentation_read_fallback"
    assert directive["tool_id"] == "web.manage"
    assert directive["arguments"]["source"] == "docs"
    assert directive["arguments"]["authority_profile"] == "network_vendor"


def test_query_loop_executes_template_recovery_through_registered_runtime():
    responses = [
        LLMResponse(tool_calls=[LLMToolCall(
            id="wrong-bgp-command",
            name="network.operations.device.manage",
            arguments={"action": "read", "connection_id": "conn-1", "commands": ["display bgp peer vpn4"]},
        )]),
        LLMResponse(content="已通过设备的 BGP 邻居语义采集获得实际结果。"),
    ]
    received: list[dict] = []

    def llm(**_kwargs):
        return responses.pop(0)

    def device_manage(arguments: dict):
        received.append(dict(arguments))
        if arguments["action"] == "read":
            output = _rejected_bgp_output()
            output["runtime_recovery"] = safe_read_recovery_directive(arguments, output)
            return output
        assert arguments == {"action": "collect", "connection_id": "conn-1", "facts": ["bgp_peers"]}
        return {
            "ok": True,
            "facts": {"bgp_peers": {"status": "collected", "observation_status": "observed"}},
        }

    registry = {
        "network.operations.device.manage": {
            "description": "network device operation",
            "args_schema": {
                "type": "object",
                "required": ["action", "connection_id"],
                "properties": {
                    "action": {"type": "string", "enum": ["read", "collect", "configure", "probe"]},
                    "connection_id": {"type": "string"},
                    "commands": {"type": "array"},
                    "facts": {"type": "array"},
                },
            },
        },
    }
    config = SSOTRuntimeConfig(max_query_loop_iterations=3)
    runtime = ToolRuntime(config)
    runtime.register("network.operations.device.manage", device_manage)
    engine = SSOTRuntimeEngine(config, llm_invoke=llm, tool_registry=registry, tool_runtime=runtime)

    outcome = asyncio.run(engine.run("查看设备 BGP 邻居", workspace_id="test", session_id="session"))

    assert [item["action"] for item in received] == ["read", "collect"]
    assert outcome.success is True
    assert outcome.metadata["execution_outcome"] == "complete"
    events = outcome.metadata["safe_read_recovery_events"]
    assert events[-1]["status"] == "recovered"


def test_query_loop_runs_official_docs_fallback_without_waiting_for_llm_choice():
    responses = [
        LLMResponse(tool_calls=[LLMToolCall(
            id="unknown-command",
            name="network.operations.device.manage",
            arguments={"action": "read", "connection_id": "conn-1", "commands": ["display proprietary foo status"]},
        )]),
        LLMResponse(content="已获取厂商官方文档证据。"),
    ]
    received: list[tuple[str, dict]] = []

    def llm(**_kwargs):
        return responses.pop(0)

    def device_manage(arguments: dict):
        received.append(("device", dict(arguments)))
        output = _rejected_bgp_output()
        output["command_results"][0]["command"] = arguments["commands"][0]
        output["runtime_recovery"] = safe_read_recovery_directive(arguments, output)
        return output

    def web_manage(arguments: dict):
        received.append(("web", dict(arguments)))
        return {"ok": True, "results": [{"title": "Official CLI reference", "url": "https://example.test/docs"}]}

    registry = {
        "network.operations.device.manage": {
            "description": "network device operation",
            "args_schema": {"type": "object", "required": ["action", "connection_id"], "properties": {
                "action": {"type": "string", "enum": ["read", "collect", "configure", "probe"]},
                "connection_id": {"type": "string"}, "commands": {"type": "array"}, "facts": {"type": "array"},
            }},
        },
        "web.manage": {
            "description": "official documentation", "args_schema": {"type": "object", "required": ["action", "query"], "properties": {
                "action": {"type": "string", "enum": ["search", "deep_search"]}, "query": {"type": "string"},
                "source": {"type": "string"}, "authority_profile": {"type": "string"}, "top_k": {"type": "integer"},
                "max_results": {"type": "integer"},
            }},
        },
    }
    config = SSOTRuntimeConfig(max_query_loop_iterations=3)
    runtime = ToolRuntime(config)
    runtime.register("network.operations.device.manage", device_manage)
    runtime.register("web.manage", web_manage)
    outcome = asyncio.run(SSOTRuntimeEngine(config, llm_invoke=llm, tool_registry=registry, tool_runtime=runtime).run(
        "查看设备私有状态", workspace_id="test", session_id="session",
    ))

    assert [name for name, _arguments in received] == ["device", "web"]
    assert received[1][1]["source"] == "docs"
    assert received[1][1]["authority_profile"] == "network_vendor"
    assert outcome.success is True


def test_semantic_recovery_completion_requires_collected_device_fact():
    from core.runtime_engine.goal_assertions import evaluate_goal_assertions

    context = StatelessContext(
        workspace_id="test", session_id="session", request_id="request", user_input="查看 BGP",
        extras={"goal_assertions": [{
            "assertion_id": "bgp-evidence", "kind": "semantic_observation_collected",
            "required_call_keys": ["collect-bgp"], "fact": "bgp_peers",
        }]},
    )
    unavailable = StreamingToolResult(
        tool_name="network.operations.device.manage", call_id="collect-bgp", ok=True,
        output={"facts": {"bgp_peers": {"status": "unavailable"}}},
    )
    collected = StreamingToolResult(
        tool_name="network.operations.device.manage", call_id="collect-bgp", ok=True,
        output={"facts": {"bgp_peers": {"status": "collected", "observation_status": "empty"}}},
    )

    assert evaluate_goal_assertions(context, [unavailable])["status"] == "failed"
    assert evaluate_goal_assertions(context, [collected])["status"] == "passed"
