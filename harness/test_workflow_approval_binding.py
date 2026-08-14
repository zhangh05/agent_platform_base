"""Workflow approvals are durable, exact-bound capabilities, not string bypasses."""
from __future__ import annotations

from agent.approval import get_approval_store, reset_approval_store_for_tests
from workflows.service import execute_workflow, resume_workflow_run, save_workflow


def _workflow(workspace_id: str, workflow_id: str = "approval_binding") -> None:
    save_workflow(workspace_id, {
        "workflow_id": workflow_id,
        "name": "approval binding",
        "nodes": [{
            "node_id": "guarded",
            "tool_id": "exec.run",
            "arguments": {
                "action": "shell",
                "command": "rm -f /tmp/lianzhi-workflow-test-never-created; echo APPROVED",
            },
            "depends_on": [],
        }],
    })


def test_forged_approval_id_cannot_execute_workflow(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    reset_approval_store_for_tests()
    _workflow("approval_ws")
    run = execute_workflow("approval_ws", "approval_binding", approvals={"guarded": "forged"})
    assert run["status"] == "failed"
    assert run["nodes"][0]["errors"] == ["invalid_approval_id"]


def test_exact_approved_action_resumes_same_workflow_run(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    reset_approval_store_for_tests()
    _workflow("approval_ws")
    waiting = execute_workflow("approval_ws", "approval_binding")
    assert waiting["status"] == "awaiting_approval"
    approval_id = waiting["nodes"][0]["approval_id"]
    request = get_approval_store("approval_ws").resolve(
        approval_id, True, workspace_id="approval_ws", resolver="test",
    )
    assert request is not None and request.allowed
    resumed = resume_workflow_run("approval_ws", waiting["run_id"], approval_id)
    assert resumed["run_id"] == waiting["run_id"]
    assert resumed["status"] == "succeeded"
    assert resumed["nodes"][-1]["summary"] == "APPROVED"
