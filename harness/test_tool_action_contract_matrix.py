"""Every merged tool action must expose and validate its real arguments."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.runtime_engine.models import ExecutionNode
from core.runtime_engine.semantic_validator import SemanticValidator
from core.tools.action_requirements import (
    ACTION_EXECUTION_CONTRACTS,
    ACTION_REQUIRED_ALL,
    ACTION_REQUIRED_ANY,
)
from core.tools.canonical_registry import CANONICAL_REGISTRY


def _registered_action_requirements() -> tuple[dict, dict]:
    """Merge domain-neutral base requirements with installed extensions."""
    required_all = dict(ACTION_REQUIRED_ALL)
    required_any = dict(ACTION_REQUIRED_ANY)
    from extensions.runtime import get_extension_tool_specs
    for spec, _handler in get_extension_tool_specs():
        requirements = (spec.metadata or {}).get("action_requirements") or {}
        for action, fields in (requirements.get("all") or {}).items():
            required_all[(spec.tool_id, action)] = tuple(fields)
        for action, groups in (requirements.get("any") or {}).items():
            required_any[(spec.tool_id, action)] = tuple(tuple(group) for group in groups)
    return required_all, required_any


REGISTERED_REQUIRED_ALL, REGISTERED_REQUIRED_ANY = _registered_action_requirements()


def test_catalog_and_runtime_agree_on_read_only_action_semantics():
    from core.runtime_engine.contracts import is_read_only_call
    from core.tools.catalog_snapshot import build_catalog_snapshot

    mismatches = []
    for tool in build_catalog_snapshot()["tools"]:
        for profile in tool.get("action_profiles") or []:
            runtime_value = is_read_only_call(
                tool["tool_id"], {"action": profile["action"]}, tool,
            )
            if runtime_value != bool(profile.get("read_only")):
                mismatches.append((tool["tool_id"], profile["action"]))
    assert mismatches == []


def test_every_base_action_has_an_explicit_execution_contract():
    missing = []
    for tool_id, actions in _action_enums(include_extensions=False).items():
        for action in actions:
            contract = ACTION_EXECUTION_CONTRACTS.get((tool_id, action))
            if not contract:
                missing.append((tool_id, action))
                continue
            assert contract["action_class"] in {"read", "write", "execute", "network", "delete"}
            assert contract["idempotency"] in {"safe_to_retry", "unsafe_to_retry"}
            assert isinstance(contract["read_only"], bool)
    assert missing == []


def _action_enums(*, include_extensions: bool = True) -> dict[str, set[str]]:
    result = {}
    for tool_id, entry in CANONICAL_REGISTRY.items():
        action = (entry.input_schema.get("properties") or {}).get("action") or {}
        if action.get("enum"):
            result[tool_id] = set(action["enum"])
    if not include_extensions:
        return result
    from extensions.runtime import get_extension_tool_specs
    for spec, _handler in get_extension_tool_specs():
        action = (spec.input_schema.get("properties") or {}).get("action") or {}
        if action.get("enum"):
            result[spec.tool_id] = set(action["enum"])
    return result


def _tool_properties(tool_id: str) -> dict:
    if tool_id in CANONICAL_REGISTRY:
        return CANONICAL_REGISTRY[tool_id].input_schema["properties"]
    from extensions.runtime import get_extension_tool_specs
    for spec, _handler in get_extension_tool_specs():
        if spec.tool_id == tool_id:
            return spec.input_schema.get("properties") or {}
    raise AssertionError(f"unknown tool_id {tool_id}")


def _validator_for_tool(tool_id: str) -> SemanticValidator:
    if tool_id in CANONICAL_REGISTRY:
        return SemanticValidator()
    from extensions.runtime import get_extension_tool_specs
    registry = {}
    for spec, _handler in get_extension_tool_specs():
        registry[spec.tool_id] = {
            "args_schema": spec.input_schema,
            "description": spec.description,
            "risk_level": spec.risk_level,
            "metadata": spec.metadata,
        }
    return SemanticValidator(registry)


def test_alias_canonical_actions_equal_public_schema_actions():
    from core.runtime_engine.action_alias import _CANONICAL_ACTIONS

    public = _action_enums(include_extensions=False)
    assert set(_CANONICAL_ACTIONS) == set(public)
    for tool_id, actions in public.items():
        assert _CANONICAL_ACTIONS[tool_id] == frozenset(actions), tool_id


def test_action_requirements_reference_public_actions_and_arguments():
    public = _action_enums()
    for requirements, is_all in ((REGISTERED_REQUIRED_ALL, True), (REGISTERED_REQUIRED_ANY, False)):
        for (tool_id, action), fields in requirements.items():
            assert tool_id in public
            assert action in public[tool_id], (tool_id, action)
            properties = _tool_properties(tool_id)
            flattened = fields if is_all else (
                field for alternatives in fields for field in alternatives
            )
            for field_name in flattened:
                assert field_name in properties, (tool_id, action, field_name)


def test_base_action_requirements_remain_domain_neutral():
    assert all(not tool_id.startswith("network.operations.") for tool_id, _action in ACTION_REQUIRED_ALL)
    assert all(not tool_id.startswith("network.operations.") for tool_id, _action in ACTION_REQUIRED_ANY)
    core_root = Path(__file__).resolve().parents[1] / "core"
    assert "network.operations." not in "\n".join(
        path.read_text(encoding="utf-8")
        for path in core_root.rglob("*.py")
    )


def test_extension_action_requirements_remain_with_the_extension():
    from extensions.runtime import get_extension_tool_specs

    specs = {spec.tool_id: spec for spec, _handler in get_extension_tool_specs()}
    device = specs["network.operations.device.manage"]
    requirements = device.metadata["action_requirements"]

    assert requirements["any"]["probe"] == (("asset_id", "host"),)
    assert requirements["any"]["read"] == (("asset_id", "host"),)
    assert device.metadata["bindable_inputs"]["probe"] == ("asset_id",)


def test_every_declared_binding_target_is_a_public_tool_argument():
    from core.tools.canonical_registry import to_tool_specs

    for spec, _handler in to_tool_specs():
        properties = (spec.input_schema or {}).get("properties") or {}
        actions = set((properties.get("action") or {}).get("enum") or [])
        declared = (spec.metadata or {}).get("bindable_inputs") or {}
        for action, fields in declared.items():
            assert action == "*" or action in actions, (spec.tool_id, action)
            assert fields, (spec.tool_id, action)
            for field in fields:
                assert field in properties, (spec.tool_id, action, field)


def test_public_schemas_expose_handler_consumed_arguments():
    expected = {
        "exec.run": {"command", "code", "description", "working_dir", "timeout", "target", "shell", "env_vars"},
        "browser.manage": {"url", "selector", "ref", "text", "script", "key", "value", "wait_selector", "wait_text", "timeout", "wait_ms", "compact", "max_elements", "full_page", "as_file", "clear_first", "direction", "amount", "tab_action", "tab_index"},
        "web.manage": {"query", "source", "url", "location", "days", "language", "units", "recency", "safe_search", "depth", "domains", "allowed_domains", "blocked_domains", "site", "vendor", "max_results", "top_k", "extract_mode", "max_length", "timeout"},
        "location.manage": {"query", "queries", "latitude", "longitude", "language", "country_code", "admin_hint", "limit"},
        "data.manage": {"text", "rows", "column", "conditions", "group_by", "metrics", "by", "order", "max_rows", "output", "index", "columns", "values", "aggfunc", "right_text", "right_rows", "on", "how"},
        "report.manage": {"title", "content", "summary", "text_a", "text_b"},
        "knowledge.manage": {"query", "limit", "level", "chunk_id", "source_id", "artifact_id", "chunk_type", "scope", "include_disabled", "include_deleted"},
        "memory.manage": {"query", "limit", "title", "content", "memory_id", "memory_type", "scope", "field", "value", "merge", "session_id", "tags"},
        "skill.manage": {"query", "limit", "skill_name", "provider_id", "tool_name", "arguments", "confirm"},
        "agent.manage": {"instruction", "profile_id", "max_turns", "background", "subtask_id", "parent_task_id"},
        "system.manage": {"run_id", "session_id", "snapshot_id", "operation", "reason", "format", "dry_run", "status", "limit", "log_level"},
        "text.analyze": {"text", "pattern"},
        "workspace.file": {"filepath", "file_id", "limit", "offset", "subdir", "pattern", "old_string", "new_string", "replace_all", "patch_text", "filename", "content", "dry_run"},
        "workspace.artifact": {"artifact_id", "query", "limit", "title", "content", "tags", "artifact_type", "status"},
        "workspace.filestore": {"file_id", "filepath"},
        "workspace.document.pdf.extract_text": {"filepath", "page_range"},
    }
    for tool_id, fields in expected.items():
        properties = set(CANONICAL_REGISTRY[tool_id].input_schema.get("properties") or {})
        assert fields <= properties, (tool_id, sorted(fields - properties))


@pytest.mark.parametrize(
    ("tool_id", "action", "permission"),
    [
        ("browser.manage", "navigate", "network"),
        ("browser.manage", "click", "network"),
        ("agent.manage", "spawn", "exec"),
        ("agent.manage", "cancel", "exec"),
        ("skill.manage", "mcp_call", "exec"),
        ("knowledge.manage", "reindex", "write"),
    ],
)
def test_action_profiles_describe_real_side_effect_permissions(tool_id, action, permission):
    from core.tools.catalog_snapshot import build_action_profiles_for_tool

    entry = CANONICAL_REGISTRY[tool_id]
    profiles = build_action_profiles_for_tool(
        tool_id,
        input_schema=entry.input_schema,
        base_permission=entry.permission_action or "read",
    )
    actual = next(profile for profile in profiles if profile["action"] == action)
    assert actual["permission_action"] == permission


def test_selfcheck_reports_real_status_instead_of_unconditional_success(monkeypatch):
    from core.tools.general_tools.runtime_tools import handle_runtime_selfcheck
    from core.tools.schemas import ToolInvocation

    class FakeResult:
        def as_dict(self):
            return {
                "status": "degraded",
                "issues": [{"code": "BROKEN_REFERENCE"}],
                "checks": {"workspace_root": "ok"},
            }

    monkeypatch.setattr("core.runtime.selfcheck.run_selfcheck", lambda _ws: FakeResult())
    result = handle_runtime_selfcheck(ToolInvocation(
        tool_id="system.manage", workspace_id="test_ws", arguments={"action": "selfcheck"},
    ))
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["selfcheck_status"] == "degraded"
    assert result["healthy"] is False
    assert result["issue_count"] == 1


def test_knowledge_reindex_propagates_service_failure(monkeypatch):
    from core.tools.general_tools.runtime_tools import handle_knowledge_reindex
    from core.tools.schemas import ToolInvocation

    monkeypatch.setattr("agent.modules.knowledge.service.reindex_source", lambda *_args: {"ok": False, "error": "source_not_found"})
    result = handle_knowledge_reindex(ToolInvocation(
        tool_id="knowledge.manage", workspace_id="test_ws", arguments={"action": "reindex", "source_id": "missing"},
    ))
    assert result["ok"] is False
    assert "source_not_found" in result["error"]


def test_knowledge_search_propagates_service_failure(monkeypatch):
    from core.tools.general_tools.runtime_tools import handle_knowledge_search
    from core.tools.schemas import ToolInvocation

    monkeypatch.setattr(
        "agent.modules.knowledge.service.search_chunks",
        lambda **_kwargs: {"ok": False, "error": "knowledge_index_unavailable"},
    )
    result = handle_knowledge_search(ToolInvocation(
        tool_id="knowledge.manage", workspace_id="test_ws", arguments={"action": "search", "query": "故障手册"},
    ))
    assert result["ok"] is False
    assert "knowledge_index_unavailable" in result["error"]


@pytest.mark.parametrize(
    ("handler_name", "governance_name", "arguments"),
    [
        ("handle_memory_confirm", "confirm_memory", {"action": "confirm", "memory_id": "mem_missing"}),
        ("handle_memory_delete_soft", "reject_memory", {"action": "delete", "memory_id": "mem_missing"}),
    ],
)
def test_memory_mutations_propagate_governance_failures(monkeypatch, handler_name, governance_name, arguments):
    import core.tools.general_tools.memory_tools as memory_tools
    from core.tools.schemas import ToolInvocation

    monkeypatch.setattr(f"storage.memory_governance.{governance_name}", lambda *_args: {"ok": False, "error": "not found"})
    handler = getattr(memory_tools, handler_name)
    result = handler(ToolInvocation(tool_id="memory.manage", workspace_id="test_ws", arguments=arguments))
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_memory_confirmation_preserves_governance_status(monkeypatch):
    from core.tools.general_tools.memory_tools import handle_memory_confirm
    from core.tools.schemas import ToolInvocation

    monkeypatch.setattr("storage.memory_governance.confirm_memory", lambda *_args: {"ok": True, "status": "active"})
    result = handle_memory_confirm(ToolInvocation(
        tool_id="memory.manage", workspace_id="test_ws", arguments={"action": "confirm", "memory_id": "mem_1"},
    ))
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["memory_status"] == "active"


@pytest.mark.parametrize(
    "tool_id,action,field_name",
    [
        (tool_id, action, field_name)
        for (tool_id, action), fields in REGISTERED_REQUIRED_ALL.items()
        for field_name in fields
    ],
)
def test_each_required_argument_is_rejected_when_missing(tool_id, action, field_name):
    args = {"action": action}
    for required in REGISTERED_REQUIRED_ALL[(tool_id, action)]:
        if required != field_name:
            args[required] = _sample_value(required)
    for alternatives in REGISTERED_REQUIRED_ANY.get((tool_id, action), ()):
        args[alternatives[0]] = _sample_value(alternatives[0])

    result = _validator_for_tool(tool_id).validate([
        ExecutionNode(id="contract", tool=tool_id, args=args),
    ])

    assert result.valid is False
    assert any(field_name in error.message for error in result.errors)


@pytest.mark.parametrize(
    "tool_id,action,alternatives",
    [
        (tool_id, action, alternatives)
        for (tool_id, action), groups in REGISTERED_REQUIRED_ANY.items()
        for alternatives in groups
    ],
)
def test_each_required_alternative_group_is_rejected_when_empty(tool_id, action, alternatives):
    args = {"action": action}
    for required in REGISTERED_REQUIRED_ALL.get((tool_id, action), ()):
        args[required] = _sample_value(required)
    for other_group in REGISTERED_REQUIRED_ANY.get((tool_id, action), ()):
        if other_group != alternatives:
            args[other_group[0]] = _sample_value(other_group[0])

    result = _validator_for_tool(tool_id).validate([
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
    for required in REGISTERED_REQUIRED_ALL.get((tool_id, action), ()):
        args.setdefault(required, _sample_value(required))
    for alternatives in REGISTERED_REQUIRED_ANY.get((tool_id, action), ()):
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

@pytest.mark.parametrize(
    ("tool_id", "action", "permission", "side_effects", "idempotency", "approval"),
    [
        ("workspace.file", "read", "read", "none", "safe_to_retry", False),
        ("workspace.file", "delete", "write", "workspace", "unsafe_to_retry", True),
        ("workspace.artifact", "save", "write", "workspace", "unsafe_to_retry", False),
        ("workspace.artifact", "delete", "write", "workspace", "unsafe_to_retry", True),
        ("workspace.filestore", "import", "write", "workspace", "unsafe_to_retry", False),
        ("agent.manage", "spawn", "exec", "task_state", "unsafe_to_retry", False),
    ],
)
def test_action_contracts_drive_catalog_risk_and_side_effects(
    tool_id, action, permission, side_effects, idempotency, approval,
):
    from core.tools.catalog_snapshot import build_action_profiles_for_tool

    entry = CANONICAL_REGISTRY[tool_id]
    profile = next(
        item for item in build_action_profiles_for_tool(
            tool_id,
            input_schema=entry.input_schema,
            base_permission=entry.permission_action or "read",
        )
        if item["action"] == action
    )
    assert profile["permission_action"] == permission
    assert profile["side_effects"] == side_effects
    assert profile["idempotency"] == idempotency
    assert profile["requires_approval"] is approval


def test_runtime_routes_do_not_expose_direct_tool_execution():
    from flask import Flask

    from backend.api.runtime_routes import register_runtime_routes

    app = Flask(__name__)
    register_runtime_routes(app)
    response = app.test_client().post(
        "/api/tools/invoke",
        json={"tool_id": "workspace.file", "arguments": {"action": "read"}},
    )
    assert response.status_code == 404


def test_ssot_engine_requires_explicit_runtime_wiring():
    from core.runtime_engine.engine import SSOTRuntimeEngine

    with pytest.raises(ValueError, match="explicitly wired ToolRuntime"):
        SSOTRuntimeEngine()


def test_pdf_extract_rejects_workspace_escape_and_non_pdf(monkeypatch, tmp_path):
    from core.tools.general_tools.pdf_tools import handle_pdf_extract_text
    from core.tools.schemas import ToolInvocation

    root = tmp_path / "workspaces"
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(root))
    target = root / "pdf_contract" / "note.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not-a-pdf")

    escaped = handle_pdf_extract_text(ToolInvocation(
        tool_id="workspace.document.pdf.extract_text",
        workspace_id="pdf_contract",
        arguments={"filepath": "../../outside.pdf"},
    ))
    assert escaped["ok"] is False

    invalid = handle_pdf_extract_text(ToolInvocation(
        tool_id="workspace.document.pdf.extract_text",
        workspace_id="pdf_contract",
        arguments={"filepath": "note.pdf"},
    ))
    assert invalid["ok"] is False
    assert "not a PDF" in invalid["error"]


def test_workspace_metadata_is_scoped_and_reports_current_files(monkeypatch, tmp_path):
    from core.tools.general_tools.file_tools import handle_ws_get_metadata
    from core.tools.schemas import ToolInvocation

    root = tmp_path / "workspaces"
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(root))
    current_files = root / "metadata_contract" / "files"
    other_files = root / "other_workspace" / "files"
    current_files.mkdir(parents=True)
    other_files.mkdir(parents=True)
    (current_files / "one.txt").write_text("one", encoding="utf-8")
    (other_files / "two.txt").write_text("two", encoding="utf-8")

    result = handle_ws_get_metadata(ToolInvocation(
        tool_id="workspace.metadata.get",
        workspace_id="metadata_contract",
        arguments={},
    ))
    assert result["ok"] is True
    assert result["workspace_id"] == "metadata_contract"
    assert result["artifact_count"] == 1


def test_action_contract_risk_is_enforced_by_real_policy():
    from core.tools.canonical_registry import to_tool_specs
    from core.tools.policy import ToolPolicy
    from core.tools.schemas import ToolInvocation

    specs = {spec.tool_id: spec for spec, _handler in to_tool_specs()}
    cases = [
        ("workspace.filestore", "import", "medium", False),
        ("workspace.artifact", "save", "medium", False),
        ("workspace.artifact", "delete", "high", True),
    ]
    for tool_id, action, risk_level, requires_approval in cases:
        decision = ToolPolicy().check(
            specs[tool_id],
            ToolInvocation(
                tool_id=tool_id,
                workspace_id="default",
                arguments={"action": action},
                requested_by="turn_runner",
            ),
        )
        assert decision.risk_level == risk_level
        assert decision.requires_approval is requires_approval
