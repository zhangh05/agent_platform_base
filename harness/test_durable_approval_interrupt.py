# Durable approval interrupt contracts.
"""Phase 4: Interrupt / Approval primitives."""

import pytest, uuid
from agent.runtime.durable.models import TaskState, RuntimeStep, _next_id
from agent.runtime.durable.store import save_task, get_task, get_events, get_checkpoints, append_event
from agent.runtime.durable.control import checkpoint_task

class TestInterrupt:
    def test_high_risk_tool_triggers_interrupt(self):
        from agent.runtime.durable.interrupt import interrupt_before_tool
        ws = f"ws_int_{uuid.uuid4().hex[:8]}"
        sid = f"sess_{uuid.uuid4().hex[:4]}"
        task = TaskState.new(workspace_id=ws, session_id=sid, run_id="r1")
        task.update_status("running"); save_task(task)

        result = interrupt_before_tool(
            task_id=task.task_id,
            ws_id=ws, session_id=sid, run_id="r1",
            step_id="s1",
            tool_invocation={"tool_id": "exec.run", "arguments": {"cmd": "rm -rf /tmp/test"}},
            risk_decision={"risk_level": "high", "reason": "Destructive command"},
        )
        assert result["ok"] is True
        assert result["status"] == "waiting_approval"
        assert result.get("approval_id")

        loaded = get_task(ws, task.task_id)
        assert loaded is not None
        assert loaded.status == "waiting_approval"
        assert loaded.pending_approval_id == result["approval_id"]

    def test_interrupt_creates_checkpoint(self):
        from agent.runtime.durable.interrupt import interrupt_before_tool
        ws = f"ws_icp_{uuid.uuid4().hex[:8]}"
        sid = f"s_{uuid.uuid4().hex[:4]}"
        task = TaskState.new(workspace_id=ws, session_id=sid)
        task.update_status("running"); save_task(task)

        interrupt_before_tool(
            task_id=task.task_id,
            ws_id=ws, session_id=sid, run_id="r1",
            step_id="s-x",
            tool_invocation={"tool_id": "exec.run", "arguments": {"cmd": "rm /tmp/x"}},
            risk_decision={"risk_level": "critical", "reason": "dangerous"},
        )
        cps = get_checkpoints(ws, task.task_id)
        assert len(cps) >= 1
        assert cps[-1].get("pending_action") is not None
        assert cps[-1]["pending_action"]["type"] == "tool_call"

    def test_interrupt_writes_approval_required_event(self):
        from agent.runtime.durable.interrupt import interrupt_before_tool
        ws = f"ws_iev_{uuid.uuid4().hex[:8]}"
        sid = f"s_{uuid.uuid4().hex[:4]}"
        task = TaskState.new(workspace_id=ws, session_id=sid)
        task.update_status("running"); save_task(task)

        interrupt_before_tool(
            task_id=task.task_id,
            ws_id=ws, session_id=sid, run_id="r1",
            step_id="s-e",
            tool_invocation={"tool_id": "exec.run", "arguments": {"cmd": "rm /tmp/x"}},
            risk_decision={"risk_level": "high", "reason": "dangerous"},
        )
        events = get_events(ws, task.task_id)
        assert any(e["type"] == "approval_required" for e in events)


class TestResumeAfterApproval:
    def test_approve_resumes_task(self):
        from agent.runtime.durable.interrupt import interrupt_before_tool, resume_after_approval
        ws = f"ws_app_{uuid.uuid4().hex[:8]}"
        sid = f"s_{uuid.uuid4().hex[:4]}"
        task = TaskState.new(workspace_id=ws, session_id=sid)
        task.update_status("running"); save_task(task)

        ir = interrupt_before_tool(
            task_id=task.task_id,
            ws_id=ws, session_id=sid, run_id="r1",
            step_id="s-app",
            tool_invocation={"tool_id": "exec.run", "arguments": {"cmd": "rm /tmp/x"}},
            risk_decision={"risk_level": "high", "reason": "dangerous"},
        )
        result = resume_after_approval(task.task_id, ws, ir["approval_id"], "approve")
        assert result["ok"] is True
        assert result["status"] == "running"

        loaded = get_task(ws, task.task_id)
        assert loaded.status == "running"
        assert loaded.pending_approval_id is None

    def test_reject_does_not_execute(self):
        from agent.runtime.durable.interrupt import interrupt_before_tool, resume_after_approval
        ws = f"ws_rej_{uuid.uuid4().hex[:8]}"
        sid = f"s_{uuid.uuid4().hex[:4]}"
        task = TaskState.new(workspace_id=ws, session_id=sid)
        task.update_status("running"); save_task(task)

        ir = interrupt_before_tool(
            task_id=task.task_id,
            ws_id=ws, session_id=sid, run_id="r1",
            step_id="s-rej",
            tool_invocation={"tool_id": "exec.run", "arguments": {"cmd": "rm /tmp/x"}},
            risk_decision={"risk_level": "high", "reason": "dangerous"},
        )
        result = resume_after_approval(task.task_id, ws, ir["approval_id"], "reject",
                                       reason="Too dangerous")
        assert result["ok"] is True
        assert result["status"] == "failed"

        loaded = get_task(ws, task.task_id)
        assert loaded.status == "failed"
        assert any("approval_rejected" in e for e in loaded.errors)

    def test_edit_args_uses_new_params(self):
        from agent.runtime.durable.interrupt import interrupt_before_tool, resume_after_approval
        ws = f"ws_ed_{uuid.uuid4().hex[:8]}"
        sid = f"s_{uuid.uuid4().hex[:4]}"
        task = TaskState.new(workspace_id=ws, session_id=sid)
        task.update_status("running"); save_task(task)

        ir = interrupt_before_tool(
            task_id=task.task_id,
            ws_id=ws, session_id=sid, run_id="r1",
            step_id="s-ed",
            tool_invocation={"tool_id": "exec.run", "arguments": {"cmd": "rm /tmp/x"}},
            risk_decision={"risk_level": "high", "reason": "dangerous"},
        )
        result = resume_after_approval(task.task_id, ws, ir["approval_id"], "edit_args",
                                       edited_args={"cmd": "ls /tmp"})
        assert result["ok"] is True
        assert result["decision"] == "edit_args"
        assert result["edited_args_keys"] == ["cmd"]

        loaded = get_task(ws, task.task_id)
        assert loaded.status == "running"

    def test_resolved_approval_not_reusable(self):
        from agent.runtime.durable.interrupt import interrupt_before_tool, resume_after_approval
        ws = f"ws_nr_{uuid.uuid4().hex[:8]}"
        sid = f"s_{uuid.uuid4().hex[:4]}"
        task = TaskState.new(workspace_id=ws, session_id=sid)
        task.update_status("running"); save_task(task)

        ir = interrupt_before_tool(
            task_id=task.task_id,
            ws_id=ws, session_id=sid, run_id="r1",
            step_id="s-nr",
            tool_invocation={"tool_id": "exec.run", "arguments": {"cmd": "rm /tmp/x"}},
            risk_decision={"risk_level": "high", "reason": "dangerous"},
        )
        # First resolve succeeds
        r1 = resume_after_approval(task.task_id, ws, ir["approval_id"], "approve")
        assert r1["ok"] is True
        # Second resolve should fail (task no longer waiting_approval)
        r2 = resume_after_approval(task.task_id, ws, ir["approval_id"], "approve")
        assert r2["ok"] is False

    def test_cross_workspace_approval_denied(self):
        from agent.runtime.durable.interrupt import resume_after_approval
        ws_a = f"ws_cw_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_cx_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws_a, session_id="s1")
        task.update_status("waiting_approval")
        task.pending_approval_id = "apr-123"; save_task(task)

        result = resume_after_approval(task.task_id, ws_b, "apr-123", "approve")
        assert result["ok"] is False


class TestRedaction:
    def test_interrupt_args_redacted(self):
        from agent.runtime.durable.interrupt import interrupt_before_tool
        ws = f"ws_red_{uuid.uuid4().hex[:8]}"
        sid = f"s_{uuid.uuid4().hex[:4]}"
        task = TaskState.new(workspace_id=ws, session_id=sid)
        task.update_status("running"); save_task(task)

        interrupt_before_tool(
            task_id=task.task_id,
            ws_id=ws, session_id=sid, run_id="r1",
            step_id="s-red",
            tool_invocation={"tool_id": "exec.run",
                             "arguments": {"cmd": "ls", "api_key": "sk-abc", "password": "pwd"}},
            risk_decision={"risk_level": "high", "reason": "test"},
        )
        cps = get_checkpoints(ws, task.task_id)
        args = cps[-1]["pending_action"]["input_args_redacted"]
        assert args.get("api_key") == "[REDACTED]"
        assert args.get("password") == "[REDACTED]"
        assert args.get("cmd") == "ls"
