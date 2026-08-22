"""Action-level retry contract coverage for every canonical tool."""

from __future__ import annotations

import asyncio
import pytest

from core.runtime_engine.contracts import (
    ALWAYS_READ_ONLY_TOOLS,
    READ_ONLY_ACTIONS,
    get_retry_contract,
    is_read_only_call,
)
from core.runtime_engine.models import ToolResult
from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
from core.runtime_engine.query_loop import StreamingToolExecutor
from core.runtime_engine.tool_runtime import ToolRuntime
from core.tools.canonical_registry import CANONICAL_REGISTRY
from agent.llm.schemas import LLMToolCall


def _actions(tool_id: str) -> list[str]:
    schema = CANONICAL_REGISTRY[tool_id].input_schema or {}
    return list(
        schema.get("properties", {}).get("action", {}).get("enum", []) or []
    )


def test_every_action_has_one_retry_classification():
    """All merged actions are explicitly read-only or conservatively mutating."""
    for tool_id in CANONICAL_REGISTRY:
        actions = _actions(tool_id)
        declared_reads = READ_ONLY_ACTIONS.get(tool_id, frozenset())
        assert declared_reads.issubset(set(actions)), (tool_id, declared_reads, actions)
        for action in actions:
            expected = tool_id in ALWAYS_READ_ONLY_TOOLS or action in declared_reads
            assert is_read_only_call(tool_id, {"action": action}) is expected


def test_read_actions_get_bounded_retry_and_writes_never_do():
    for tool_id in CANONICAL_REGISTRY:
        actions = _actions(tool_id)
        if not actions:
            contract = get_retry_contract(tool_id, {})
            assert contract is not None
            if tool_id in ALWAYS_READ_ONLY_TOOLS:
                assert contract.idempotent is True
                assert contract.side_effect == "read"
                assert contract.max_retries >= 1
            else:
                assert contract.idempotent is False
                assert contract.max_retries == 0
            continue

        for action in actions:
            contract = get_retry_contract(tool_id, {"action": action})
            assert contract is not None
            if is_read_only_call(tool_id, {"action": action}):
                assert contract.idempotent is True, (tool_id, action)
                assert contract.side_effect == "read", (tool_id, action)
                assert contract.max_retries >= 1, (tool_id, action)
            else:
                assert contract.idempotent is False, (tool_id, action)
                assert contract.max_retries == 0, (tool_id, action)


@pytest.mark.parametrize(
    ("tool_id", "action", "expected_read"),
    [
        ("workspace.file", "list", True),
        ("workspace.file", "write", False),
        ("knowledge.manage", "search", True),
        ("knowledge.manage", "import", False),
        ("memory.manage", "profile_get", True),
        ("memory.manage", "profile_set", False),
        ("system.manage", "health", True),
        ("system.manage", "session_checkpoint", False),
        ("workspace.filestore", "references", True),
        ("workspace.filestore", "import", False),
        ("agent.manage", "status", True),
        ("agent.manage", "spawn", False),
        ("agent.manage", "cancel", False),
        ("skill.manage", "list", True),
        ("skill.manage", "mcp_call", False),
        ("browser.manage", "screenshot", False),
        ("report.manage", "document", True),
    ],
)
def test_high_risk_merged_tool_boundaries(tool_id, action, expected_read):
    assert is_read_only_call(tool_id, {"action": action}) is expected_read


def test_extension_action_profiles_drive_parallel_and_retry_semantics():
    from extensions.runtime import get_extension_tool_specs

    # Loading extension specs synchronizes their contracts into the common
    # runtime without naming extension actions in the base contract table.
    get_extension_tool_specs()
    expected = {
        ("network.operations.baseline", "list"): True,
        ("network.operations.baseline", "diff"): True,
        ("network.operations.baseline", "create"): False,
        ("network.operations.inspection", "list"): True,
        ("network.operations.inspection", "get"): True,
        ("network.operations.inspection", "run"): False,
        ("network.operations.inspection", "cancel"): False,
        ("network.operations.inspection", "retry"): False,
        ("network.operations.device.manage", "probe"): True,
        ("network.operations.device.manage", "read"): True,
    }
    for (tool_id, action), read_only in expected.items():
        assert is_read_only_call(tool_id, {"action": action}) is read_only
        contract = get_retry_contract(tool_id, {"action": action})
        assert contract is not None
        assert contract.idempotent is read_only
        assert (contract.max_retries > 0) is read_only


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("request timed out", "TOOL_TIMEOUT"),
        ("HTTP 503 upstream unavailable", "HTTP_503"),
        ("Security check failed: Forbidden import", "POLICY_BLOCKED"),
        ("query is required", "ARGS_INVALID"),
        ("artifact not found", "ARGS_INVALID"),
        ("task_not_found", "ARGS_INVALID"),
        ("blocked: private/local URLs not allowed", "POLICY_BLOCKED"),
        ("session_workspace_mismatch", "POLICY_BLOCKED"),
        ("permission denied", "CREDENTIAL_ACCESS"),
        ("unexpected provider crash", "TOOL_EXCEPTION"),
    ],
)
def test_generic_handler_errors_resolve_to_retry_semantics(error, expected):
    result = ToolResult(
        node_id="n1",
        tool="knowledge.manage",
        success=False,
        error=error,
        error_code="TOOL_RETURNED_NOT_OK",
    )
    assert StreamingToolExecutor._retry_error_code(result) == expected


@pytest.mark.parametrize(
    ("tool_id", "action", "expected_calls", "expected_ok"),
    [
        ("workspace.file", "list", 2, True),
        ("workspace.file", "write", 1, False),
        ("knowledge.manage", "search", 2, True),
        ("knowledge.manage", "import", 1, False),
        ("system.manage", "health", 2, True),
        ("system.manage", "session_checkpoint", 1, False),
        ("workspace.filestore", "references", 2, True),
        ("workspace.filestore", "import", 1, False),
        ("agent.manage", "status", 2, True),
        ("agent.manage", "spawn", 1, False),
    ],
)
def test_action_level_retry_executes_only_safe_reads(
    tool_id, action, expected_calls, expected_ok,
):
    config = SSOTRuntimeConfig(max_retries_per_node=1)
    runtime = ToolRuntime(config)
    calls = {"count": 0}

    async def transient_once(_args):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"ok": False, "error": "request timed out"}
        return {"ok": True, "value": "recovered"}

    runtime.register(tool_id, transient_once)
    executor = StreamingToolExecutor(runtime, config)
    ctx = StatelessContext("default", "s1", "r1", "test")
    results = asyncio.run(
        executor.execute(
            [LLMToolCall(id="call_1", name=tool_id, arguments={"action": action})],
            ctx=ctx,
        )
    )

    assert calls["count"] == expected_calls
    assert results[0].ok is expected_ok
