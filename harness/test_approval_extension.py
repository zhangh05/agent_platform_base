from __future__ import annotations

from types import SimpleNamespace

import pytest

from extensions.approval import service as approval
from extensions.network_operations import service as network


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_MASTER_KEY", "test-approval-extension-master-key")


def _connection_and_skill(workspace_id: str, *, approval_enabled: bool):
    device = network.save_device(workspace_id, {
        "name": "CE-1", "host": "127.0.0.1", "vendor": "h3c",
    })
    connection = network.save_connection(workspace_id, {
        "device_id": device["device_id"], "protocol": "telnet", "port": 30001,
        "auth_method": "none",
    }, auto_test=False)
    skill = network.save_skill(workspace_id, {
        "name": "变更 Skill",
        "device_ids": [device["device_id"]],
        "connection_ids": [connection["connection_id"]],
        "approval_enabled": approval_enabled,
    })
    return device, connection, skill


def _request(workspace_id: str, skill: dict, connection: dict):
    context = network.resolve_workbench_selection(workspace_id, {"skill_id": skill["skill_id"]})
    return {
        "tool_id": "network.operations.device.manage",
        "call_id": "call_exact",
        "workspace_id": workspace_id,
        "session_id": "session_approval",
        "run_id": "run_approval",
        "arguments": {
            "action": "configure",
            "connection_id": connection["connection_id"],
            "commands": ["system-view", "interface LoopBack 100", "description exact text", "return"],
            "timeout": 31,
        },
        "workbench_context": context,
    }


def test_approval_is_absent_when_the_skill_has_not_enabled_it(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _, connection, skill = _connection_and_skill("default", approval_enabled=False)
    assert approval.prepare_network_operation(_request("default", skill, connection)) is None


def test_approval_freezes_exact_operation_and_revalidates_current_scope(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device, connection, skill = _connection_and_skill("default", approval_enabled=True)
    record = approval.prepare_network_operation(_request("default", skill, connection))
    assert record is not None
    assert record["status"] == "pending"
    assert record["commands"] == ["system-view", "interface LoopBack 100", "description exact text", "return"]
    assert record["target"]["connection"]["connection_id"] == connection["connection_id"]
    assert approval.get_operation("default", record["operation_id"])["digest"] == record["digest"]

    approval.decide_operation("default", record["operation_id"], "approve")
    # A device edit changes the frozen target identity and must invalidate the
    # prepared operation before any socket can be opened.
    network.save_device("default", {**device, "name": "CE-1-renamed"})
    claimed = approval.claim_execution("default", record["operation_id"])
    assert claimed["status"] == "invalidated"
    assert claimed["invalidated_reason"] == "server_scope_or_connection_changed"


def test_approval_interceptor_returns_server_owned_interruption(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _, connection, skill = _connection_and_skill("default", approval_enabled=True)
    outcome = approval.execution_interceptor(_request("default", skill, connection))
    assert outcome and outcome["action"] == "suspend"
    assert outcome["kind"] == "approval"
    record = approval.get_operation("default", outcome["interruption_id"])
    assert record and record["digest"] == outcome["payload"]["digest"]


def test_runtime_interceptor_uses_the_enabled_extension_hook(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _, connection, skill = _connection_and_skill("default", approval_enabled=True)
    from extensions.runtime import reset_extension_cache_for_tests
    from core.runtime_engine.execution_interceptors import before_tool_execution

    reset_extension_cache_for_tests()
    request = _request("default", skill, connection)
    interception = before_tool_execution(
        tool_id=request["tool_id"], call_id=request["call_id"], arguments=request["arguments"],
        ctx=SimpleNamespace(
            workspace_id="default", session_id="session_approval", run_id="run_approval",
            request_id="request_approval", extras={"workbench_context": request["workbench_context"]},
        ),
    )
    assert interception is not None
    assert interception.kind == "approval"
    assert interception.as_tool_output()["executed"] is False


def test_external_interruption_is_a_waiting_task_not_a_completed_task():
    from agent.runtime.task_state import _derive_status

    assert _derive_status(
        {"assertions": {}, "failure": {}}, {"execution_outcome": "waiting_external_input"}, True,
    ) == ("waiting_user", "await_external_decision")


def test_approval_reject_never_claims_or_executes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _, connection, skill = _connection_and_skill("default", approval_enabled=True)
    record = approval.prepare_network_operation(_request("default", skill, connection))
    rejected = approval.decide_operation("default", record["operation_id"], "reject")
    assert rejected["status"] == "rejected"
    with pytest.raises(ValueError, match="operation_not_approved"):
        approval.claim_execution("default", record["operation_id"])
