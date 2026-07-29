# Trajectory evaluation contracts.
"""Phase 10: Trajectory evaluation tests."""

import pytest, uuid
from agent.runtime.durable.trajectory import (
    build_trajectory, persist_trajectory, get_trajectory,
    list_trajectories, evaluate_trajectory, save_feedback,
    TrajectoryRecord, TrajectoryMetrics,
)


class TestTrajectoryBuilder:
    def test_build_from_task(self):
        from agent.runtime.durable.store import save_task, get_task
        from agent.runtime.durable.models import TaskState, RuntimeStep
        ws = f"ws_t10_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1", run_id="r1",
                             user_goal="Test trajectory building")
        task.add_step(RuntimeStep(step_id="s1", task_id=task.task_id, kind="model", title="Step 1"))
        task.update_status("succeeded"); save_task(task)

        traj = build_trajectory(task.task_id, ws)
        assert traj is not None
        assert traj.task_id == task.task_id
        assert traj.final_status == "succeeded"
        assert traj.metrics.task_success is True

    def test_trajectory_persists_and_reads(self):
        ws = f"ws_tp_{uuid.uuid4().hex[:8]}"
        rec = TrajectoryRecord(task_id="t1", workspace_id=ws,
                               session_id="s1", final_status="succeeded",
                               user_goal="test", metrics=TrajectoryMetrics(task_success=True))
        persist_trajectory(rec)
        loaded = get_trajectory(rec.trajectory_id, ws)
        assert loaded is not None
        assert loaded["metrics"]["task_success"] is True

    def test_list_trajectories(self):
        ws = f"ws_tl_{uuid.uuid4().hex[:8]}"
        r1 = TrajectoryRecord(task_id="t1", workspace_id=ws, session_id="s1",
                              final_status="succeeded")
        r2 = TrajectoryRecord(task_id="t2", workspace_id=ws, session_id="s1",
                              final_status="failed")
        persist_trajectory(r1); persist_trajectory(r2)
        items = list_trajectories(ws)
        assert len(items) >= 2


class TestMetrics:
    def test_task_failure_detected(self):
        from agent.runtime.durable.store import save_task
        from agent.runtime.durable.models import TaskState
        ws = f"ws_mf_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.update_status("failed"); save_task(task)
        traj = build_trajectory(task.task_id, ws)
        assert traj.metrics.task_success is False

    def test_unverified_completion_detected(self):
        from agent.runtime.durable.store import save_task
        from agent.runtime.durable.models import TaskState
        ws = f"ws_uv_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1")
        task.update_status("succeeded"); save_task(task)
        traj = build_trajectory(task.task_id, ws)
        assert traj.metrics.unverified_completion is True


class TestFeedback:
    def test_feedback_not_active_memory(self):
        ws = f"ws_fb_{uuid.uuid4().hex[:8]}"
        rec = TrajectoryRecord(task_id="t1", workspace_id=ws, session_id="s1",
                               final_status="succeeded")
        persist_trajectory(rec)

        result = save_feedback(rec.trajectory_id, ws,
                               {"rating": 4, "comment": "Good answer but missing error handling"})
        assert result["ok"] is True
        saved = get_trajectory(rec.trajectory_id, ws)
        assert saved["final_status"] == "succeeded"
        assert saved["metrics"]["task_success"] is False
        assert saved["user_feedback"]["rating"] == 4

    def test_feedback_preserves_full_trajectory(self):
        ws = f"ws_fb_{uuid.uuid4().hex[:8]}"
        rec = TrajectoryRecord(
            task_id="t2",
            workspace_id=ws,
            session_id="s2",
            final_status="succeeded",
            runtime_events=[{"type": "tool_completed"}],
            tool_calls=[{"tool_id": "system.health"}],
            checkpoints=[{"checkpoint_id": "cp1"}],
            artifacts=["art1"],
        )
        rec.metrics.tool_call_count = 1
        persist_trajectory(rec)

        assert save_feedback(rec.trajectory_id, ws, {"rating": 5})["ok"] is True

        saved = get_trajectory(rec.trajectory_id, ws)
        assert saved["runtime_events"] == [{"type": "tool_completed"}]
        assert saved["tool_calls"] == [{"tool_id": "system.health"}]
        assert saved["checkpoints"] == [{"checkpoint_id": "cp1"}]
        assert saved["artifacts"] == ["art1"]
        assert saved["metrics"]["tool_call_count"] == 1
