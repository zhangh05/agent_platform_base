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
    assert props["action"]["enum"] == ["search", "fetch", "weather", "weather_batch", "deep_search"]
    assert "location" in props
    assert props["locations"]["maxItems"] == 10
    assert "days" in props
    assert props["days"]["description"].lower().find("forecast") >= 0


def test_agent_contract_exposes_current_runtime_actions():
    from core.runtime_engine.contracts import get_contract
    from agent.runtime.durable.subagent import BUILTIN_PROFILES

    properties = get_contract("agent.manage").input_schema["properties"]
    actions = properties["action"]["enum"]
    assert actions == ["spawn", "list", "get", "status", "cancel", "merge"]
    assert "instruction" in properties
    assert "profile_id" in properties
    assert properties["profile_id"]["enum"] == list(BUILTIN_PROFILES)
    assert properties["profile_id"]["default"] == "research_agent"
    assert "max_turns" in properties
    assert "background" in properties


def test_llm_projection_preserves_complete_schema_constraints():
    from core.tools.canonical_registry import to_openai_tools

    tools = {item["function"]["name"]: item["function"] for item in to_openai_tools()}
    weather = tools["web__manage"]["parameters"]
    assert weather["additionalProperties"] is False
    assert weather["properties"]["locations"]["minItems"] == 2
    assert weather["properties"]["locations"]["maxItems"] == 10
    assert tools["agent__manage"]["parameters"]["properties"]["profile_id"]["enum"] == [
        "research_agent", "file_agent", "data_agent",
    ]
    assert "data_agent=" in tools["agent__manage"]["parameters"]["properties"]["profile_id"]["description"]
    assert "write_artifact=>filename+content" in tools["workspace__file"]["description"]
    assert tools["workspace__file"]["description"].count("Use for workspace paths and managed attachments") == 1


def test_all_base_tool_schemas_reject_unpublished_arguments():
    from core.tools.canonical_registry import CANONICAL_REGISTRY

    assert len(CANONICAL_REGISTRY) == 17
    for tool_id, entry in CANONICAL_REGISTRY.items():
        assert entry.input_schema.get("additionalProperties") is False, tool_id


def test_semantic_validator_rejects_unknown_profile_and_array_cardinality():
    from core.runtime_engine.models import ExecutionNode
    from core.runtime_engine.semantic_validator import SemanticValidator

    result = SemanticValidator().validate([
        ExecutionNode(
            id="bad_profile",
            tool="agent.manage",
            args={"action": "spawn", "instruction": "research", "profile_id": "general_agent"},
        ),
        ExecutionNode(
            id="bad_batch",
            tool="web.manage",
            args={"action": "weather_batch", "locations": ["上海"], "invented": True},
        ),
    ])

    assert result.valid is False
    by_code = {error.code: error for error in result.errors}
    assert by_code["ARG_ENUM_INVALID"].details["allowed_values"] == [
        "research_agent", "file_agent", "data_agent",
    ]
    assert by_code["ARG_LENGTH_INVALID"].details["minItems"] == 2
    assert by_code["UNKNOWN_ARGUMENT"].details["field"] == "invented"


def test_non_action_enum_error_cannot_trigger_action_alias_repair():
    from core.runtime_engine.models import ExecutionNode
    from core.runtime_engine.semantic_validator import SemanticValidator

    result = SemanticValidator().validate([
        ExecutionNode(
            id="bad_source",
            tool="web.manage",
            args={"action": "search", "query": "test", "source": "web_search"},
        ),
    ])

    assert result.valid is False
    error = next(error for error in result.errors if error.details.get("field") == "source")
    assert error.code == "ARG_ENUM_INVALID"


def test_tool_executor_enforces_the_same_closed_schema_and_cardinality():
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    from core.tools.executor import _validate_arguments

    schema = CANONICAL_REGISTRY["web.manage"].input_schema
    errors = _validate_arguments({
        "action": "weather_batch",
        "locations": ["上海"],
        "invented": True,
    }, schema)

    assert any("Unknown field: 'invented'" in error for error in errors)
    assert any("below minimum 2" in error for error in errors)


def test_weather_batch_coordinate_items_keep_a_closed_recursive_contract():
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    from core.tools.executor import _validate_arguments

    schema = CANONICAL_REGISTRY["web.manage"].input_schema
    valid = _validate_arguments({
        "action": "weather_batch",
        "locations": [
            {"name": "广州", "latitude": 23.1291, "longitude": 113.2644},
            "深圳",
        ],
    }, schema)
    missing_pair = _validate_arguments({
        "action": "weather_batch",
        "locations": [{"name": "广州", "latitude": 23.1291}, "深圳"],
    }, schema)
    unknown = _validate_arguments({
        "action": "weather_batch",
        "locations": [{"location": "广州"}, "深圳"],
    }, schema)

    assert valid == []
    assert any("does not match any allowed schema" in error for error in missing_pair)
    assert any("does not match any allowed schema" in error for error in unknown)


def test_tool_executor_classifies_schema_rejection_as_non_retryable():
    from core.tools.executor import ToolExecutor
    from core.tools.registry import ToolRegistry
    from core.tools.schemas import ToolInvocation, ToolSpec

    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(
            tool_id="test.closed_schema",
            name="closed schema",
            description="test",
            category="tool",
            input_schema={
                "type": "object",
                "properties": {"subtask_id": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
        lambda _invocation: {"ok": True},
    )

    result = ToolExecutor(registry).execute(ToolInvocation(
        tool_id="test.closed_schema",
        arguments={"task_id": "wrong-id"},
    ))

    assert result.status == "blocked"
    assert result.output["executed"] is False
    assert result.output["error_code"] == "TOOL_ARGUMENT_VALIDATION_FAILED"
    assert result.output["retryable"] is False


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
