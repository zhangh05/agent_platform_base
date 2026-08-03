"""Every merged tool action must expose and validate its real arguments."""

from __future__ import annotations

import pytest

from core.runtime_engine.models import ExecutionNode
from core.runtime_engine.semantic_validator import SemanticValidator
from core.tools.action_requirements import ACTION_REQUIRED_ALL, ACTION_REQUIRED_ANY
from core.tools.canonical_registry import CANONICAL_REGISTRY


def _action_enums() -> dict[str, set[str]]:
    result = {}
    for tool_id, entry in CANONICAL_REGISTRY.items():
        action = (entry.input_schema.get("properties") or {}).get("action") or {}
        if action.get("enum"):
            result[tool_id] = set(action["enum"])
    return result


def test_alias_canonical_actions_equal_public_schema_actions():
    from core.runtime_engine.action_alias import _CANONICAL_ACTIONS

    public = _action_enums()
    assert set(_CANONICAL_ACTIONS) == set(public)
    for tool_id, actions in public.items():
        assert _CANONICAL_ACTIONS[tool_id] == frozenset(actions), tool_id


def test_action_requirements_reference_public_actions_and_arguments():
    public = _action_enums()
    for requirements in (ACTION_REQUIRED_ALL, ACTION_REQUIRED_ANY):
        for (tool_id, action), fields in requirements.items():
            assert tool_id in public
            assert action in public[tool_id], (tool_id, action)
            properties = CANONICAL_REGISTRY[tool_id].input_schema["properties"]
            flattened = fields if requirements is ACTION_REQUIRED_ALL else (
                field for alternatives in fields for field in alternatives
            )
            for field_name in flattened:
                assert field_name in properties, (tool_id, action, field_name)


def test_public_schemas_expose_handler_consumed_arguments():
    expected = {
        "exec.run": {"command", "code", "description", "working_dir", "timeout", "target", "shell", "env_vars"},
        "browser.manage": {"url", "selector", "ref", "text", "script", "key", "value", "wait_selector", "wait_text", "timeout", "wait_ms", "compact", "max_elements", "full_page", "as_file", "clear_first", "direction", "amount", "tab_action", "tab_index"},
        "web.manage": {"query", "source", "url", "location", "days", "language", "units", "recency", "safe_search", "depth", "domains", "allowed_domains", "blocked_domains", "site", "vendor", "max_results", "top_k", "extract_mode", "max_length", "timeout"},
        "data.manage": {"text", "rows", "column", "conditions", "group_by", "metrics", "by", "order", "max_rows", "output", "index", "columns", "values", "aggfunc", "right_text", "right_rows", "on", "how"},
        "device.manage": {"asset_id", "host", "port", "vendor", "username", "password", "auth_method", "private_key", "passphrase", "host_key_fingerprint", "accept_host_key", "commands", "timeout"},
        "report.manage": {"title", "content", "summary", "text_a", "text_b"},
        "knowledge.manage": {"query", "limit", "level", "chunk_id", "source_id", "artifact_id", "chunk_type", "scope", "include_disabled", "include_deleted"},
        "memory.manage": {"query", "limit", "title", "content", "memory_id", "memory_type", "scope", "field", "value", "merge", "session_id", "tags"},
        "skill.manage": {"query", "limit", "skill_name", "provider_id", "tool_name", "arguments", "confirm"},
        "agent.manage": {"instruction", "profile_id", "max_turns", "background", "child_session_id", "subtask_id", "parent_task_id"},
        "system.manage": {"run_id", "session_id", "snapshot_id", "operation", "reason", "format", "dry_run", "status", "limit", "log_level"},
        "text.analyze": {"text", "pattern"},
        "workspace.file": {"filepath", "limit", "offset", "subdir", "pattern", "old_string", "new_string", "replace_all", "patch_text", "filename", "content", "dry_run"},
        "workspace.artifact": {"artifact_id", "query", "limit", "title", "content", "tags", "artifact_type", "status"},
        "workspace.filestore": {"file_id", "filepath"},
        "workspace.document.pdf.extract_text": {"filepath", "page_range"},
    }
    for tool_id, fields in expected.items():
        properties = set(CANONICAL_REGISTRY[tool_id].input_schema.get("properties") or {})
        assert fields <= properties, (tool_id, sorted(fields - properties))


@pytest.mark.parametrize(
    "tool_id,action,field_name",
    [
        (tool_id, action, field_name)
        for (tool_id, action), fields in ACTION_REQUIRED_ALL.items()
        for field_name in fields
    ],
)
def test_each_required_argument_is_rejected_when_missing(tool_id, action, field_name):
    args = {"action": action}
    for required in ACTION_REQUIRED_ALL[(tool_id, action)]:
        if required != field_name:
            args[required] = _sample_value(required)
    for alternatives in ACTION_REQUIRED_ANY.get((tool_id, action), ()):
        args[alternatives[0]] = _sample_value(alternatives[0])

    result = SemanticValidator().validate([
        ExecutionNode(id="contract", tool=tool_id, args=args),
    ])

    assert result.valid is False
    assert any(field_name in error.message for error in result.errors)


@pytest.mark.parametrize(
    "tool_id,action,alternatives",
    [
        (tool_id, action, alternatives)
        for (tool_id, action), groups in ACTION_REQUIRED_ANY.items()
        for alternatives in groups
    ],
)
def test_each_required_alternative_group_is_rejected_when_empty(tool_id, action, alternatives):
    args = {"action": action}
    for required in ACTION_REQUIRED_ALL.get((tool_id, action), ()):
        args[required] = _sample_value(required)
    for other_group in ACTION_REQUIRED_ANY.get((tool_id, action), ()):
        if other_group != alternatives:
            args[other_group[0]] = _sample_value(other_group[0])

    result = SemanticValidator().validate([
        ExecutionNode(id="contract", tool=tool_id, args=args),
    ])

    assert result.valid is False
    assert any("requires one of" in error.message for error in result.errors)


def _sample_value(field_name: str):
    if field_name in {"rows", "right_rows", "conditions", "commands", "tags"}:
        return [{"value": 1}] if field_name not in {"commands", "tags"} else ["value"]
    if field_name == "value":
        return False
    return "value"


def test_representative_read_actions_return_non_generic_runtime_summaries(temp_dirs):
    from core.tools.context import ToolRuntimeContext
    from core.tools.integration import get_default_tool_runtime_client
    from storage.workspace_store import ensure_workspace

    workspace_id = "tool_matrix_ws"
    ensure_workspace(workspace_id)
    context = ToolRuntimeContext(
        workspace_id=workspace_id,
        session_id="matrix-session",
        run_id="matrix-run",
        requested_by="turn_runner",
        module="contract_matrix",
    )
    cases = [
        ("system.manage", {"action": "selfcheck"}),
        ("system.manage", {"action": "tasks"}),
        ("system.manage", {"action": "audit_log"}),
        ("data.manage", {"action": "parse", "rows": [{"a": 1}]}),
        ("report.manage", {"action": "diff", "text_a": "a", "text_b": "b"}),
        ("knowledge.manage", {"action": "search", "query": "none"}),
        ("knowledge.manage", {"action": "list"}),
        ("knowledge.manage", {"action": "chunk"}),
        ("memory.manage", {"action": "search", "query": "none"}),
        ("skill.manage", {"action": "list"}),
        ("agent.manage", {"action": "list"}),
        ("agent.manage", {"action": "status"}),
        ("text.analyze", {"action": "redact", "text": "hello"}),
        ("text.analyze", {"action": "match", "text": "abc", "pattern": "a"}),
        ("workspace.file", {"action": "list"}),
        ("workspace.file", {"action": "glob", "pattern": "*"}),
        ("workspace.artifact", {"action": "list"}),
        ("workspace.metadata.get", {}),
    ]

    client = get_default_tool_runtime_client()
    for tool_id, arguments in cases:
        result = client.invoke(tool_id, arguments, context=context)
        assert result.status == "succeeded", (tool_id, result.errors)
        assert result.summary
        assert "without structured output" not in result.summary
        assert result.summary != f"Tool {tool_id} completed"


@pytest.mark.parametrize(
    "tool_id,action,field_name,value",
    [
        ("web.manage", "search", "safe_search", True),
        ("web.manage", "weather", "days", "10"),
        ("browser.manage", "snapshot", "compact", "true"),
        ("data.manage", "sort", "by", "column"),
        ("skill.manage", "mcp_call", "arguments", []),
    ],
)
def test_semantic_validator_rejects_wrong_public_argument_types(tool_id, action, field_name, value):
    args = {"action": action, field_name: value}
    for required in ACTION_REQUIRED_ALL.get((tool_id, action), ()):
        args.setdefault(required, _sample_value(required))
    for alternatives in ACTION_REQUIRED_ANY.get((tool_id, action), ()):
        args.setdefault(alternatives[0], _sample_value(alternatives[0]))

    result = SemanticValidator().validate([
        ExecutionNode(id="contract", tool=tool_id, args=args),
    ])

    assert result.valid is False
    assert any(error.code == "ARG_TYPE_MISMATCH" and field_name in error.message for error in result.errors)


def test_semantic_validator_rejects_public_argument_range_violation():
    result = SemanticValidator().validate([
        ExecutionNode(
            id="contract",
            tool="web.manage",
            args={"action": "weather", "location": "广州", "days": 11},
        ),
    ])

    assert result.valid is False
    assert any(error.code == "ARG_RANGE_INVALID" and "days" in error.message for error in result.errors)
