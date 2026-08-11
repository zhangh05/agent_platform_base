"""SSOT Runtime contract visibility must stay in sync with canonical tools.

The planner sees canonical schemas through ToolRuntimeClient. The semantic
validator must validate against the same public schema; otherwise valid LLM
plans can be rejected or under-parameterized.
"""

from __future__ import annotations


def test_ssot_runtime_contracts_use_canonical_input_schemas():
    from core.runtime_engine.contracts import BUILTIN_CONTRACTS
    from core.tools.canonical_registry import CANONICAL_REGISTRY, to_tool_specs

    # Base tools are defined in the canonical registry. Extensions are loaded
    # dynamically and deliberately add their own contracts, so equality would
    # reject every valid extension as stale test data.
    canonical_ids = set(CANONICAL_REGISTRY)
    assert canonical_ids <= set(BUILTIN_CONTRACTS)
    for tool_id, entry in CANONICAL_REGISTRY.items():
        assert BUILTIN_CONTRACTS[tool_id].input_schema == entry.input_schema

    extension_ids = {
        spec.tool_id for spec, _handler in to_tool_specs()
        if spec.tool_id not in canonical_ids
    }
    assert set(BUILTIN_CONTRACTS) - canonical_ids == extension_ids


def test_web_weather_contract_exposes_forecast_arguments():
    from core.runtime_engine.contracts import get_contract

    schema = get_contract("web.manage").input_schema
    props = schema["properties"]
    assert props["action"]["enum"] == ["search", "fetch", "weather", "deep_search"]
    assert "location" in props
    assert "days" in props
    assert props["days"]["description"].lower().find("forecast") >= 0


def test_agent_contract_exposes_current_runtime_actions():
    from core.runtime_engine.contracts import get_contract

    properties = get_contract("agent.manage").input_schema["properties"]
    actions = properties["action"]["enum"]
    assert actions == ["spawn", "list", "get", "status", "cancel", "merge"]
    assert "instruction" in properties
    assert "profile_id" in properties
    assert "max_turns" in properties
    assert "background" in properties


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
    assert data.get("local_timezone") == "Asia/Shanghai"
    assert data.get("current_time_local", "").endswith("+08:00")
    assert len(data.get("local_date", "")) == 10


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


def test_semantic_validator_rejects_agent_spawn_without_instruction():
    from core.runtime_engine.models import ExecutionNode
    from core.runtime_engine.semantic_validator import SemanticValidator

    result = SemanticValidator().validate([
        ExecutionNode(id="spawn", tool="agent.manage", args={"action": "spawn"}),
    ])

    assert result.valid is False
    assert any("instruction" in error.message for error in result.errors)
