"""Risk policy contract for direct authorization and hard blocks."""

import asyncio
from unittest import mock

from core.runtime_engine.models import ExecutionNode, SSOTRuntimeConfig
from core.runtime_engine.risk_policy import RiskPolicyEngine, _check_destructive_command, _check_system_destroy
from core.runtime_engine.tool_runtime import ToolRuntime


def _node(node_id: str, tool: str, **args) -> ExecutionNode:
    return ExecutionNode(id=node_id, tool=tool, args=args)


def test_read_calls_are_allowed():
    result = RiskPolicyEngine().assess([
        _node("a", "knowledge.manage", action="search"),
        _node("b", "data.manage", action="filter"),
    ])
    assert result.safe_to_run is True
    assert result.hard_block is False


def test_product_write_is_not_blocked_by_generic_risk_metadata():
    result = RiskPolicyEngine().assess([
        _node("a", "workspace.artifact", action="delete", artifact_id="art_test"),
    ])
    assert result.safe_to_run is True
    assert result.hard_block is False


def test_large_batches_warn_without_creating_control_plane_state():
    config = SSOTRuntimeConfig(rp_max_exec_allow=2, rp_max_tool_nodes_allow=3)
    result = RiskPolicyEngine(config).assess([
        _node(str(i), "exec.run", command=f"printf {i}") for i in range(5)
    ])
    assert result.hard_block is False
    assert any("command batch" in warning.lower() for warning in result.warnings)
    assert any("tool batch" in warning.lower() for warning in result.warnings)


def test_destructive_host_commands_are_hard_blocked():
    for command in ("rm -f /tmp/x", "rm -rf /tmp/build", "git reset --hard HEAD~1", "docker system prune -af"):
        result = RiskPolicyEngine().assess([_node("danger", "exec.run", command=command)])
        assert result.hard_block is True, command
        assert result.safe_to_run is False


def test_system_destroy_and_credential_combinations_are_hard_blocked():
    for command in ("rm -rf /", "rm -rf /tmp/build && cat ~/.ssh/id_rsa"):
        result = RiskPolicyEngine().assess([_node("danger", "exec.run", command=command)])
        assert result.hard_block is True


def test_destructive_pattern_helpers():
    assert _check_destructive_command("rm -f /tmp/x") == "rm -f"
    assert _check_destructive_command("docker system prune -af") == "docker system prune"
    assert _check_destructive_command("ls -la") == ""
    assert _check_system_destroy("rm -rf /") == "rm -rf /"
    assert _check_system_destroy("rm -rf /tmp/build") == ""


def test_engine_does_not_dispatch_a_hard_blocked_call():
    from agent.llm.schemas import LLMResponse, LLMToolCall
    from core.runtime_engine.engine import SSOTRuntimeEngine

    def llm(**_kwargs):
        return LLMResponse(tool_calls=[LLMToolCall(
            id="call-root-delete",
            name="exec.run",
            arguments={"action": "shell", "command": "rm -rf /"},
        )])

    async def drive():
        config = SSOTRuntimeConfig()
        engine = SSOTRuntimeEngine(
            config=config,
            llm_invoke=llm,
            tool_registry={"exec.run": {"description": "", "args_schema": {
                "required": ["command"], "properties": {"command": {"type": "string"}},
            }}},
            tool_runtime=ToolRuntime(config),
        )
        handler = mock.AsyncMock()
        engine.register_tool("exec.run", handler)
        result = await engine.run("test")
        assert result.success is False
        assert result.metadata.get("hard_block") is True
        handler.assert_not_awaited()

    asyncio.run(drive())
