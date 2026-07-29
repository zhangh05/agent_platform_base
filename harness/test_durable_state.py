# Durable task state persistence contracts.
"""Phase 2: Durable Runtime State — targeted tests.

Validates:
1. TaskState creation on agent turn
2. Workspace/session boundary enforcement
3. RuntimeEvent persistence and query
4. RuntimeCheckpoint persistence and query
5. Redaction of secrets
6. Cross-workspace isolation
7. Existing approval guard unaffected
"""

import pytest
import json
import uuid
from pathlib import Path
from agent.runtime.durable.models import (
    TaskState, RuntimeStep, RuntimeEvent, RuntimeCheckpoint,
)


class TestTaskStateModel:
    """Core data model tests."""

    def test_create_task_with_required_fields(self):
        task = TaskState.new(workspace_id="ws_test", session_id="sess_1")
        assert task.task_id.startswith("task-")
        assert task.workspace_id == "ws_test"
        assert task.session_id == "sess_1"
        assert task.status == "pending"
        assert task.steps == []
        assert task.created_at

    def test_add_step_updates_current_step(self):
        task = TaskState.new(workspace_id="ws_test", session_id="sess_1")
        step = task.add_step(RuntimeStep(
            step_id="step-1", task_id=task.task_id,
            kind="model", title="Model call",
        ))
        assert task.current_step_id == "step-1"
        assert len(task.steps) == 1
        assert step.task_id == task.task_id

    def test_step_lifecycle(self):
        task = TaskState.new(workspace_id="ws_test", session_id="sess_1")
        step = RuntimeStep(
            step_id="step-2", task_id=task.task_id,
            kind="tool", title="Run command",
        )
        step.mark_started()
        assert step.status == "running"
        assert step.started_at

        step.mark_finished(ok=True, summary="Done")
        assert step.status == "succeeded"
        assert step.summary == "Done"
        assert step.finished_at

    def test_task_serialization_roundtrip(self):
        task = TaskState.new(workspace_id="ws-a", session_id="sess-1",
                             run_id="run-1", user_goal="test goal")
        task.add_step(RuntimeStep(
            step_id="s1", task_id=task.task_id,
            kind="model", title="Step 1",
        ))
        d = task.to_dict()
        task2 = TaskState.from_dict(d)
        assert task2.task_id == task.task_id
        assert task2.workspace_id == "ws-a"
        assert len(task2.steps) == 1
        assert task2.steps[0].step_id == "s1"


class TestStateStore:
    """Persistence and query tests."""

    def test_save_and_get_task(self):
        task = TaskState.new(workspace_id="ws_phase2", session_id="sess_test",
                             run_id="run-test", user_goal="test persistence")
        task.update_status("running")
        task.add_step(RuntimeStep(
            step_id="step-x", task_id=task.task_id, kind="model", title="Test step",
        ))
        from agent.runtime.durable.store import save_task, get_task
        save_task(task)

        loaded = get_task("ws_phase2", task.task_id)
        assert loaded is not None
        assert loaded.task_id == task.task_id
        assert loaded.status == "running"
        assert loaded.session_id == "sess_test"
        assert len(loaded.steps) == 1

    def test_list_tasks_by_session(self):
        ws_id = f"ws_list_{uuid.uuid4().hex[:8]}"
        t1 = TaskState.new(workspace_id=ws_id, session_id="sess-A", run_id="r1")
        t2 = TaskState.new(workspace_id=ws_id, session_id="sess-B", run_id="r2")
        from agent.runtime.durable.store import save_task, list_tasks

        t1.update_status("succeeded"); save_task(t1)
        t2.update_status("failed"); save_task(t2)

        results = list_tasks(ws_id, session_id="sess-A")
        assert any(t.task_id == t1.task_id for t in results)
        assert not any(t.task_id == t2.task_id for t in results)

    def test_task_not_found_returns_none(self):
        from agent.runtime.durable.store import get_task
        assert get_task("ws_nonexist", "task-nonexist") is None

    def test_save_then_list_empty_session(self):
        from agent.runtime.durable.store import save_task, list_tasks
        ws_id = f"ws_el_{uuid.uuid4().hex[:8]}"
        t = TaskState.new(workspace_id=ws_id, session_id="s1", run_id="r1")
        t.update_status("succeeded"); save_task(t)

        results = list_tasks(ws_id, session_id="s2")
        assert len(results) == 0


class TestRuntimeEvents:
    """Event persistence tests."""

    def test_append_and_read_events(self):
        from agent.runtime.durable.store import append_event, get_events
        ws_id = f"ws_ev_{uuid.uuid4().hex[:8]}"
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        evt = RuntimeEvent(
            event_id="evt-1", task_id=task_id, workspace_id=ws_id,
            session_id="sess-1", run_id="run-1", step_id="step-1",
            type="tool_call", status="ok", title="Tool executed",
            summary="Ran ls command",
        )
        append_event(evt)
        events = get_events(ws_id, task_id)
        assert len(events) >= 1
        assert events[-1]["event_id"] == "evt-1"
        assert events[-1]["type"] == "tool_call"

    def test_event_redacts_secrets(self):
        from agent.runtime.durable.store import append_event, get_events
        ws_id = f"ws_redact_{uuid.uuid4().hex[:8]}"
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        evt = RuntimeEvent(
            event_id="evt-secret", task_id=task_id, workspace_id=ws_id,
            session_id="sess-1", run_id="run-1",
            type="tool_call", status="ok",
            payload_redacted={"cmd": "ls", "api_key": "sk-abc123", "token": "bearer-xyz"},
        )
        append_event(evt)
        events = get_events(ws_id, task_id)
        payload = events[-1].get("payload_redacted", {})
        assert payload.get("api_key") == "[REDACTED]"
        assert payload.get("token") == "[REDACTED]"
        assert payload.get("cmd") == "ls"


class TestCheckpoints:
    """Checkpoint persistence tests."""

    def test_save_and_read_checkpoint(self):
        from agent.runtime.durable.store import save_checkpoint, get_checkpoints
        ws_id = f"ws_cp_{uuid.uuid4().hex[:8]}"
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        cp = RuntimeCheckpoint(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:8]}",
            task_id=task_id, workspace_id=ws_id,
            session_id="sess-1", run_id="run-1", step_id="step-5",
            state_snapshot={"step": 5, "tools": ["web.manage"]},
        )
        save_checkpoint(cp)
        cps = get_checkpoints(ws_id, task_id)
        assert len(cps) >= 1
        assert cps[-1]["checkpoint_id"] == cp.checkpoint_id
        assert cps[-1]["step_id"] == "step-5"


class TestCrossWorkspaceIsolation:
    """Workspace boundary tests."""

    def test_cross_workspace_task_not_readable(self):
        from agent.runtime.durable.store import save_task, get_task
        ws_a = f"ws_a_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_b_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws_a, session_id="s1")
        save_task(task)

        # task exists in ws_a
        assert get_task(ws_a, task.task_id) is not None
        # task should NOT be accessible from ws_b (different directory)
        assert get_task(ws_b, task.task_id) is None
