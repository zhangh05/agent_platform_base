# Durable checkpoint, cancel, retry, and resume contracts.
"""Phase 3: Runtime control — checkpoint, cancel, retry, resume."""

import pytest, uuid
from agent.runtime.durable.models import (
    TaskState, RuntimeStep, RuntimeEvent,
    _next_id,
)
from agent.runtime.durable.control import (
    checkpoint_task, cancel_task, retry_step, resume_task,
)
from agent.runtime.durable.store import (
    save_task, get_task, get_events, get_checkpoints,
    append_event,
)


class TestCheckpoint:
    """Checkpoint at critical execution points."""

    def test_checkpoint_after_task_start(self):
        ws = f"ws_cp_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1", run_id="r1")
        task.update_status("running"); save_task(task)

        cp = checkpoint_task(task.task_id, ws, reason="task_started")
        assert cp is not None
        assert cp.task_id == task.task_id
        assert cp.workspace_id == ws

        cps = get_checkpoints(ws, task.task_id)
        assert len(cps) >= 1
        assert cps[-1]["checkpoint_id"] == cp.checkpoint_id

    def test_checkpoint_before_tool(self):
        ws = f"ws_cpt_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.add_step(RuntimeStep(step_id="s-tool", task_id=task.task_id,
                                   kind="tool", title="Run command"))
        task.update_status("running"); save_task(task)

        cp = checkpoint_task(task.task_id, ws, reason="tool_start",
                             step_id="s-tool")
        assert cp is not None
        loaded = get_checkpoints(ws, task.task_id)
        assert loaded[-1]["step_id"] == "s-tool"

    def test_checkpoint_on_failed_task(self):
        ws = f"ws_cpf_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.update_status("failed")
        task.errors.append("connection refused"); save_task(task)

        cp = checkpoint_task(task.task_id, ws, reason="failure")
        assert cp is not None
        events = get_events(ws, task.task_id)
        assert any(e["type"] == "checkpoint_created" for e in events)

    def test_checkpoint_payload_no_secrets(self):
        ws = f"ws_cps_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.update_status("running"); save_task(task)

        # Use a pending_action with secret keys (these get redacted)
        from agent.runtime.durable.control import checkpoint_task as cp_with_action
        cp = cp_with_action(task.task_id, ws,
                           pending_action={"tool": "exec.run", "api_key": "sk-secret", "cmd": "ls"})
        snapshot = cp.state_snapshot
        # pending_action.api_key should be redacted in stored payload
        pa = cp.pending_action
        assert pa is not None
        assert pa.get("api_key") in (None, "[REDACTED]")
        assert pa.get("cmd") == "ls"


class TestCancel:
    """Cancel semantics."""

    def test_cancel_running_task(self):
        ws = f"ws_can_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.add_step(RuntimeStep(step_id="s1", task_id=task.task_id,
                                   kind="model", status="running"))
        task.current_step_id = "s1"
        task.update_status("running"); save_task(task)

        result = cancel_task(task.task_id, ws)
        assert result["ok"] is True
        assert result["status"] == "cancelled"

        loaded = get_task(ws, task.task_id)
        assert loaded.status == "cancelled"

    def test_cancel_writes_event(self):
        ws = f"ws_cev_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.update_status("running"); save_task(task)

        cancel_task(task.task_id, ws)
        events = get_events(ws, task.task_id)
        assert any(e["type"] == "task_cancelled" for e in events)

    def test_cancel_idempotent(self):
        ws = f"ws_cid_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.update_status("succeeded"); save_task(task)

        r1 = cancel_task(task.task_id, ws)  # already succeeded
        r2 = cancel_task(task.task_id, ws)  # repeat
        assert r1["ok"] is True
        assert r1["status"] == "succeeded"
        assert r2["ok"] is True

    def test_cross_workspace_cancel_rejected(self):
        ws_a = f"ws_ca_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_cb_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws_a, session_id="s1")
        save_task(task)

        result = cancel_task(task.task_id, ws_b)
        assert result["ok"] is False


class TestRetry:
    """Retry step safety."""

    def test_retry_readonly_step_allowed(self):
        ws = f"ws_rr_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.add_step(RuntimeStep(step_id="s-read", task_id=task.task_id,
                                   kind="model", status="failed",
                                   title="Search web"))
        save_task(task)

        result = retry_step(task.task_id, "s-read", ws)
        assert result["ok"] is True
        assert "new_step_id" in result

    def test_retry_destructive_step_denied(self):
        ws = f"ws_rd_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.add_step(RuntimeStep(step_id="s-rm", task_id=task.task_id,
                                   kind="tool", status="failed",
                                   tool_id="exec.run",
                                   title="Remove files",
                                   summary="rm -rf /tmp/test"))
        save_task(task)

        result = retry_step(task.task_id, "s-rm", ws)
        assert result["ok"] is False
        assert result.get("retry_not_supported") is True

    def test_retry_creates_attempt_not_overwrite(self):
        ws = f"ws_ra_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.add_step(RuntimeStep(step_id="s-model", task_id=task.task_id,
                                   kind="model", status="failed"))
        save_task(task)

        result = retry_step(task.task_id, "s-model", ws)
        loaded = get_task(ws, task.task_id)
        # original step still present
        assert any(s.step_id == "s-model" for s in loaded.steps)
        # new retry attempt present
        assert any(s.step_id == result["new_step_id"] for s in loaded.steps)

    def test_retry_can_only_retry_failed(self):
        ws = f"ws_rf_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.add_step(RuntimeStep(step_id="s-ok", task_id=task.task_id,
                                   kind="model", status="succeeded"))
        save_task(task)

        result = retry_step(task.task_id, "s-ok", ws)
        assert result["ok"] is False


class TestResume:
    """Resume from checkpoint."""

    def test_resume_no_checkpoint_returns_error(self):
        ws = f"ws_rnc_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.update_status("interrupted"); save_task(task)

        result = resume_task(task.task_id, ws)
        assert result["ok"] is False
        assert result.get("resume_not_supported") is True

    def test_resume_from_checkpoint(self):
        ws = f"ws_res_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1", run_id="r1")
        task.add_step(RuntimeStep(step_id="s-3", task_id=task.task_id,
                                   kind="model", title="Model #3"))
        task.current_step_id = "s-3"
        task.update_status("interrupted"); save_task(task)

        # Create checkpoint first
        cp = checkpoint_task(task.task_id, ws, reason="interrupted_at_step_3")
        assert cp is not None

        result = resume_task(task.task_id, ws)
        assert result["ok"] is True
        assert result["status"] == "running"
        assert result["current_step_id"] == "s-3"

        loaded = get_task(ws, task.task_id)
        assert loaded.status == "running"

    def test_resume_writes_event(self):
        ws = f"ws_rwe_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.update_status("interrupted"); save_task(task)
        checkpoint_task(task.task_id, ws, reason="test")

        resume_task(task.task_id, ws)
        events = get_events(ws, task.task_id)
        assert any(e["type"] == "task_resumed" for e in events)

    def test_cross_workspace_resume_rejected(self):
        ws_a = f"ws_ra_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_rb_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws_a, session_id="s1")
        save_task(task)

        result = resume_task(task.task_id, ws_b)
        assert result["ok"] is False


class TestMidExecutionPersistence:
    """Phase 3: mid-execution state persistence."""

    def test_task_saved_mid_execution(self):
        """Simulate mid-execution save — task exists on disk before completion."""
        ws = f"ws_mid_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1", run_id="r1")
        task.update_status("running")
        task.add_step(RuntimeStep(step_id="s-mid", task_id=task.task_id,
                                   kind="model", status="running"))
        save_task(task)  # mid-execution save

        # Verify task is readable mid-flight
        loaded = get_task(ws, task.task_id)
        assert loaded is not None
        assert loaded.status == "running"
        assert len(loaded.steps) == 1
        assert loaded.steps[0].status == "running"
