"""RiskPolicy block and approval boundaries.

Verifies the three-way risk gate with configurable thresholds:
  - exec: ≤5 allow, >5≤20 approval, >20 hard_block
  - total: ≤20 allow, >20≤50 approval, >50 hard_block
"""

import asyncio
import json
from unittest import mock

import pytest

from core.runtime_engine.models import ExecutionNode, SSOTRuntimeConfig
from core.runtime_engine.risk_policy import (
    RiskPolicyEngine,
    _check_destructive_command,
    _check_system_destroy,
)


# ── Helpers ────────────────────────────────────────────────────────────

@pytest.fixture
def risk_engine():
    return RiskPolicyEngine()


def _calls(nodes: list[ExecutionNode]) -> list[ExecutionNode]:
    return nodes


def _node(idx: str, tool: str, **args) -> ExecutionNode:
    return ExecutionNode(id=idx, tool=tool, args=args)


def _make_speng(**cfg_overrides):
    from core.runtime_engine.engine import SSOTRuntimeEngine
    cfg_kwargs = {}
    cfg_kwargs.update(cfg_overrides)
    cfg = SSOTRuntimeConfig(**cfg_kwargs)

    def mock_llm(**kw):
        return json.dumps({"nodes": []})

    return SSOTRuntimeEngine(config=cfg, llm_invoke=mock_llm, tool_registry={})


# ── Tests: allow (safe to run) ─────────────────────────────────────────

def test_allow_readonly_tools(risk_engine):
    calls = _calls([
        _node("a", "knowledge.manage", action="search"),
        _node("b", "data.manage", action="filter"),
        _node("c", "text.analyze", action="classify"),
    ])
    result = risk_engine.assess(calls)
    assert result.hard_block is False
    assert result.requires_approval is False
    assert result.safe_to_run is True


def test_artifact_delete_requires_explicit_approval(risk_engine):
    result = risk_engine.assess([
        _node("delete-artifact", "workspace.artifact", action="delete", artifact_id="art_test"),
    ])

    assert result.hard_block is False
    assert result.requires_approval is True
    assert result.approval_reason == "artifact_delete"
    assert result.approval_nodes == ["delete-artifact"]


def test_3_exec_no_approval_trigger(risk_engine):
    """≤5 harmless exec commands do not require approval."""
    nodes = [_node(str(i), "exec.run", command=f"cmd{i}") for i in range(3)]
    calls = _calls(nodes)
    result = risk_engine.assess(calls)
    assert result.requires_approval is False
    assert result.hard_block is False
    assert result.approval_reason == ""  # no count-based reason


def test_5_exec_borderline(risk_engine):
    """Exactly 5 exec → no count-based approval trigger."""
    nodes = [_node(str(i), "exec.run", command=f"cmd{i}") for i in range(5)]
    calls = _calls(nodes)
    result = risk_engine.assess(calls)
    assert result.hard_block is False
    assert result.requires_approval is False


# ── Tests: count-based batches are warnings only ───────────────────────

def test_6_exec_large_batch(risk_engine):
    """6 exec → warning only, not approval."""
    nodes = [_node(str(i), "exec.run", command=f"cmd{i}") for i in range(6)]
    calls = _calls(nodes)
    result = risk_engine.assess(calls)
    assert result.requires_approval is False
    assert result.hard_block is False
    assert result.approval_reason == ""
    assert any("command batch" in w.lower() for w in result.warnings)


def test_20_exec_warning_only(risk_engine):
    """20 exec → warning only, NOT hard block."""
    nodes = [_node(str(i), "exec.run", command=f"cmd{i}") for i in range(20)]
    calls = _calls(nodes)
    result = risk_engine.assess(calls)
    assert result.requires_approval is False
    assert result.hard_block is False
    assert any("command batch" in w.lower() for w in result.warnings)


def test_21_total_nodes_large_batch(risk_engine):
    """21 total tool nodes → warning only."""
    nodes = [_node(str(i), "knowledge.manage", action="search") for i in range(21)]
    calls = _calls(nodes)
    result = risk_engine.assess(calls)
    assert result.requires_approval is False
    assert result.hard_block is False
    assert result.approval_reason == ""
    assert any("tool batch" in w.lower() for w in result.warnings)


def test_50_total_nodes_warning_only(risk_engine):
    """50 total nodes → warning only, NOT hard block."""
    nodes = [_node(str(i), "knowledge.manage", action="search") for i in range(50)]
    calls = _calls(nodes)
    result = risk_engine.assess(calls)
    assert result.requires_approval is False
    assert result.hard_block is False
    assert any("tool batch" in w.lower() for w in result.warnings)


# ── Tests: count-based batches never hard-block ────────────────────────

def test_21_exec_warning_not_hard_block(risk_engine):
    """21 exec → warning only; runtime budgets cap execution."""
    nodes = [_node(str(i), "exec.run", command=f"cmd{i}") for i in range(21)]
    calls = _calls(nodes)
    result = risk_engine.assess(calls)
    assert result.requires_approval is False
    assert result.hard_block is False
    assert result.blocked_reason == ""


def test_51_total_nodes_warning_not_hard_block(risk_engine):
    """51 total nodes → warning only; runtime budgets cap execution."""
    nodes = [_node(str(i), "knowledge.manage", action="search") for i in range(51)]
    calls = _calls(nodes)
    result = risk_engine.assess(calls)
    assert result.requires_approval is False
    assert result.hard_block is False
    assert result.blocked_reason == ""


# ── Tests: destructive commands (approval) ─────────────────────────────

def test_rm_f_approval(risk_engine):
    calls = _calls([_node("a", "exec.run", command="rm -f /tmp/test.txt")])
    result = risk_engine.assess(calls)
    assert result.requires_approval is True
    assert result.hard_block is False
    assert result.approval_reason == "destructive_command"
    assert len(result.approval_details) == 1
    assert result.approval_details[0]["risk_reason"] == "rm -f"


def test_rm_rf_approval(risk_engine):
    calls = _calls([_node("a", "exec.run", command="rm -rf /tmp/build/")])
    result = risk_engine.assess(calls)
    assert result.requires_approval is True
    assert result.hard_block is False


def test_git_reset_hard_approval(risk_engine):
    calls = _calls([_node("a", "exec.run", command="git reset --hard HEAD~1")])
    result = risk_engine.assess(calls)
    assert result.requires_approval is True
    assert result.approval_reason == "destructive_command"


# ── Tests: hard_block (system destroy) ─────────────────────────────────

def test_rm_rf_root_hard_block(risk_engine):
    calls = _calls([_node("a", "exec.run", command="rm -rf /")])
    result = risk_engine.assess(calls)
    assert result.hard_block is True
    assert result.safe_to_run is False


def test_del_windows_hard_block(risk_engine):
    calls = _calls([_node("a", "exec.run", command="del C:\\Windows")])
    result = risk_engine.assess(calls)
    assert result.hard_block is True


# ── Tests: destructive pattern detection ───────────────────────────────

def test_destructive_patterns():
    assert _check_destructive_command("rm -f /tmp/x") == "rm -f"
    assert _check_destructive_command("rm -rf build/") == "rm -rf"
    assert _check_destructive_command("del /f /s *.tmp") == "del /f"
    assert _check_destructive_command("chmod -R 777 /var/www") == "chmod -R 777"
    assert _check_destructive_command("docker system prune -af") == "docker system prune"
    assert _check_destructive_command("kubectl delete pod my-pod") == "kubectl delete"
    assert _check_destructive_command("ls -la") == ""


def test_system_destroy_patterns():
    assert _check_system_destroy("rm -rf /") == "rm -rf /"
    assert _check_system_destroy("rm -rf /*") == "rm -rf /*"
    assert _check_system_destroy("del C:\\Windows\\system32") == "del C:\\Windows"
    assert _check_system_destroy("del C:\\Users") == "del C:\\Users"
    assert _check_system_destroy("format C:") == "format C:"
    assert _check_system_destroy("rm -rf /tmp/build") == ""


# ── Tests: destructive + credential combo → hard_block ─────────────────

def test_rm_rf_plain_approval(risk_engine):
    """Plain rm -rf /tmp/build → approval_required, NOT hard_block."""
    calls = _calls([_node("a", "exec.run", command="rm -rf /tmp/build")])
    result = risk_engine.assess(calls)
    assert result.requires_approval is True
    assert result.hard_block is False
    assert result.approval_reason == "destructive_command"


def test_rm_rf_with_credential_hard_block(risk_engine):
    """rm -rf /tmp/build && cat ~/.ssh/id_rsa → hard_block (credential access)."""
    calls = _calls([_node("a", "exec.run",
                      command="rm -rf /tmp/build && cat ~/.ssh/id_rsa")])
    result = risk_engine.assess(calls)
    assert result.hard_block is True
    assert result.safe_to_run is False
    assert "command" in result.blocked_reason.lower() or \
           "credential" in result.blocked_reason.lower() or \
           "private" in result.blocked_reason.lower()


def test_git_reset_with_credential_hard_block(risk_engine):
    """git reset --hard && cat ~/.ssh/id_rsa → hard_block."""
    calls = _calls([_node("a", "exec.run",
                      command="git reset --hard && cat ~/.ssh/id_rsa")])
    result = risk_engine.assess(calls)
    assert result.hard_block is True


def test_docker_prune_plain_approval(risk_engine):
    """docker system prune -af → approval_required, NOT hard_block."""
    calls = _calls([_node("a", "exec.run", command="docker system prune -af")])
    result = risk_engine.assess(calls)
    assert result.requires_approval is True
    assert result.hard_block is False
    assert result.approval_reason == "destructive_command"


def test_docker_prune_with_credential_hard_block(risk_engine):
    """docker system prune -af && cat ~/.ssh/id_rsa → hard_block."""
    calls = _calls([_node("a", "exec.run",
                      command="docker system prune -af && cat ~/.ssh/id_rsa")])
    result = risk_engine.assess(calls)
    assert result.hard_block is True


# ── Tests: pipeline ────────────────────────────────────────────────────

def test_approval_handler_resumes_exact_call():
    # harness/conftest.py does not install the async hook used by
    # core.runtime_engine/conftest.py, and pytest-asyncio is not a project
    # dependency. We dispatch through asyncio.run() here so the
    # test still works without the plugin (matches the pattern in
    # harness/test_alias_drift.py).
    import asyncio
    from unittest import mock as _mock
    from core.runtime_engine.engine import SSOTRuntimeEngine
    config = SSOTRuntimeConfig()

    def mock_llm(**kw):
        return json.dumps({"nodes": [
            {"id": "n1", "tool": "exec.run",
             "args": {"action": "shell", "command": "rm -f /tmp/agent-platform-base-test-file"}},
        ]})

    registry = {"exec.run": {"description": "", "args_schema": {
        "required": ["command"], "properties": {"command": {"type": "string"}},
    }}}

    async def _drive():
        approvals = []

        async def approve(_ctx, gate):
            approvals.append(gate)
            return True

        engine = SSOTRuntimeEngine(
            config=config,
            llm_invoke=mock_llm,
            tool_registry=registry,
            approval_handler=approve,
        )
        engine.register_tool("exec.run", _mock.AsyncMock(return_value={"ok": True}))

        result = await engine.run("test")
        assert result.success
        assert result.node_success_count == 1
        assert len(approvals) == 1

    asyncio.run(_drive())


def test_hard_block_denied_approval():
    import asyncio
    from unittest import mock as _mock
    from core.runtime_engine.engine import SSOTRuntimeEngine
    config = SSOTRuntimeConfig()

    def mock_llm(**kw):
        return json.dumps({"nodes": [
            {"id": "n1", "tool": "exec.run",
             "args": {"action": "shell", "command": "rm -rf /"}},
        ]})

    registry = {"exec.run": {"description": "", "args_schema": {
        "required": ["command"], "properties": {"command": {"type": "string"}},
    }}}

    async def _drive():
        engine = SSOTRuntimeEngine(config=config, llm_invoke=mock_llm, tool_registry=registry)
        engine.register_tool("exec.run", _mock.AsyncMock())

        result = await engine.run("test")
        assert result.success is False
        assert result.metadata.get("hard_block") is True

    asyncio.run(_drive())



def test_custom_thresholds_exec():
    """Custom config controls warning threshold only."""
    cfg = SSOTRuntimeConfig(rp_max_exec_allow=2, rp_max_exec_approval=4)
    engine = RiskPolicyEngine(cfg)
    # 3 exec → warning
    nodes = [_node(str(i), "exec.run", command=f"cmd{i}") for i in range(3)]
    calls = _calls(nodes)
    result = engine.assess(calls)
    assert result.requires_approval is False
    assert any("command batch" in w.lower() for w in result.warnings)
    # 5 exec → still warning, not hard_block
    nodes2 = [_node(str(i), "exec.run", command=f"cmd{i}") for i in range(5)]
    calls2 = _calls(nodes2)
    result2 = engine.assess(calls2)
    assert result2.hard_block is False


def test_custom_thresholds_tools():
    """Custom config controls tool-batch warning threshold only."""
    cfg = SSOTRuntimeConfig(rp_max_tool_nodes_allow=3, rp_max_tool_nodes_approval=6)
    engine = RiskPolicyEngine(cfg)
    # 5 nodes → warning
    nodes = [_node(str(i), "knowledge.manage", action="search") for i in range(5)]
    calls = _calls(nodes)
    result = engine.assess(calls)
    assert result.requires_approval is False
    assert result.hard_block is False
    assert any("tool batch" in w.lower() for w in result.warnings)
    # 7 nodes → still warning, not hard_block
    nodes2 = [_node(str(i), "knowledge.manage", action="search") for i in range(7)]
    calls2 = _calls(nodes2)
    result2 = engine.assess(calls2)
    assert result2.hard_block is False
