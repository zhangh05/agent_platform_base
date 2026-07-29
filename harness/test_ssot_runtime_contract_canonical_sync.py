"""SSOT Runtime contract visibility must stay in sync with canonical tools.

The planner sees canonical schemas through ToolRuntimeClient. The semantic
validator must validate against the same public schema; otherwise valid LLM
plans can be rejected or under-parameterized.
"""

from __future__ import annotations


def test_ssot_runtime_contracts_use_canonical_input_schemas():
    from core.runtime_engine.contracts import BUILTIN_CONTRACTS
    from core.tools.canonical_registry import CANONICAL_REGISTRY

    assert set(BUILTIN_CONTRACTS) == set(CANONICAL_REGISTRY)
    for tool_id, entry in CANONICAL_REGISTRY.items():
        assert BUILTIN_CONTRACTS[tool_id].input_schema == entry.input_schema


def test_web_weather_contract_exposes_forecast_arguments():
    from core.runtime_engine.contracts import get_contract

    schema = get_contract("web.manage").input_schema
    props = schema["properties"]
    assert props["action"]["enum"] == ["search", "fetch", "weather", "deep_search", "list"]
    assert "location" in props
    assert "days" in props
    assert props["days"]["description"].lower().find("forecast") >= 0


def test_agent_contract_exposes_current_runtime_actions():
    from core.runtime_engine.contracts import get_contract

    actions = get_contract("agent.manage").input_schema["properties"]["action"]["enum"]
    assert actions == ["spawn", "list", "get", "status", "cancel", "merge"]


def test_system_contract_exposes_local_info_action():
    from core.runtime_engine.contracts import get_contract
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    from core.tools.schemas import ToolInvocation

    actions = get_contract("system.manage").input_schema["properties"]["action"]["enum"]
    assert "local_info" in actions
    result = CANONICAL_REGISTRY["system.manage"].handler(ToolInvocation(
        tool_id="system.manage",
        arguments={"workspace_id": "default", "action": "local_info"},
        requested_by="turn_runner",
    ))
    assert result.get("ok") is True
    data = result.get("data") or result
    assert data.get("hostname")
    assert "ipv4_addresses" in data


def test_semantic_validator_accepts_future_weather_forecast_args():
    from core.runtime_engine.models import ExecutionNode
    from core.runtime_engine.semantic_validator import SemanticValidator

    calls = [
        ExecutionNode(
            id="weather_10d",
            tool="web.manage",
            args={"action": "weather", "location": "杭州", "days": 10},
        ),
    ]
    result = SemanticValidator().validate(calls)
    assert result.valid, [e.message for e in result.errors]
