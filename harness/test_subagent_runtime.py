# Subagent runtime isolation and lifecycle contracts.
"""Phase 9: Subagent Runtime tests."""

import pytest, uuid
from agent.runtime.durable.subagent import (
    get_profile, create_subagent_task, run_subagent_task,
    merge_subagent_result, BUILTIN_PROFILES, cancel_subagent_task,
    list_subagent_tasks, reconcile_subagent_tasks, start_subagent_task,
)


class _FakeAgentResult:
    ok = True
    final_response = "review complete"
    events = []


def _fake_run_turn(session, turn, services=None, **kwargs):
    return _FakeAgentResult()


class TestSubagentProfiles:
    def test_base_profiles_exist(self):
        assert set(BUILTIN_PROFILES) == {
            "research_agent", "file_agent", "data_agent",
        }

    def test_research_agent_is_read_scoped(self):
        p = get_profile("research_agent")
        assert p.allowed_action_classes == ["read", "network"]
        assert "knowledge.manage" in p.allowed_tools
        assert "web.manage" in p.allowed_tools
        assert "workspace.file" in p.allowed_tools
        assert not p.can_execute_commands
        assert not p.can_modify_files

    def test_data_agent_is_data_scoped(self):
        p = get_profile("data_agent")
        assert p.allowed_action_classes == ["read", "write"]
        assert "data.manage" in p.allowed_tools
        assert "report.manage" in p.allowed_tools
        assert not p.can_execute_commands


class TestSubagentTask:
    def test_create_subagent_binds_to_parent(self):
        ws = f"ws_sa_{uuid.uuid4().hex[:8]}"
        result = create_subagent_task(
            parent_task_id="task-123", workspace_id=ws,
            session_id="s1", profile_id="research_agent",
            goal="Research OSPF adjacency instability",
        )
        assert result["ok"]
        assert result["subtask_id"].startswith("sub-")

    def test_unknown_profile_rejected(self):
        result = create_subagent_task(
            parent_task_id="t1", workspace_id="ws_x",
            session_id="s1", profile_id="nonexistent",
            goal="test",
        )
        assert result["ok"] is False

    def test_workspace_required(self):
        result = create_subagent_task(
            parent_task_id="t1", workspace_id="",
            session_id="s1", profile_id="research_agent",
            goal="test",
        )
        assert result["ok"] is False


class TestSubagentRuntime:
    def test_research_agent_runs_with_profile_limits(self, monkeypatch):
        monkeypatch.setattr("agent.runtime.ssot_runtime.run_ssot_turn", _fake_run_turn)
        ws = f"ws_rt_{uuid.uuid4().hex[:8]}"
        cr = create_subagent_task("t1", ws, "s1", "research_agent", "Research workspace evidence")
        assert cr["ok"]

        r = run_subagent_task(cr["subtask_id"], ws)
        assert r["ok"]
        assert r["status"] in ("succeeded", "failed")

    def test_profile_max_steps_reaches_ssot_config(self, monkeypatch):
        import agent.runtime.ssot_runtime as runtime

        class _Client:
            def list_tools(self):
                return []

        monkeypatch.setattr(runtime, "_tool_runtime_client", lambda: _Client())
        engine = runtime._build_engine(
            workspace_id="ws_steps",
            session_id="s1",
            run_id="r1",
            trace_id="t1",
            requested_by="subagent",
            max_query_loop_iterations=3,
        )
        assert engine._config.max_query_loop_iterations == 3

    def test_timeout_is_failed_not_user_cancelled(self, monkeypatch):
        monkeypatch.setattr(
            "agent.runtime.durable.subagent._run_ssot_runtime_with_timeout",
            lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("budget expired")),
        )
        ws = f"ws_timeout_{uuid.uuid4().hex[:8]}"
        cr = create_subagent_task("t1", ws, "s1", "research_agent", "Research")
        result = run_subagent_task(cr["subtask_id"], ws)
        assert result["status"] == "failed"
        assert "timed out" in result["summary"].lower()

    def test_cross_workspace_run_blocked(self):
        ws_a = f"ws_sa9_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_sb9_{uuid.uuid4().hex[:8]}"
        cr = create_subagent_task("t1", ws_a, "s1", "research_agent", "test")
        r = run_subagent_task(cr["subtask_id"], ws_b)
        assert r["ok"] is False

    def test_merge_subagent_result(self, monkeypatch):
        monkeypatch.setattr("agent.runtime.ssot_runtime.run_ssot_turn", _fake_run_turn)
        ws = f"ws_mg_{uuid.uuid4().hex[:8]}"
        cr = create_subagent_task("t-parent", ws, "s1", "research_agent", "Research")
        r = run_subagent_task(cr["subtask_id"], ws)
        assert r["ok"]

        m = merge_subagent_result("t-parent", cr["subtask_id"], ws)
        assert m["ok"]
        assert m["merged"] is True

    def test_merge_cross_parent_rejected(self):
        ws = f"ws_mp_{uuid.uuid4().hex[:8]}"
        cr = create_subagent_task("t-real", ws, "s1", "research_agent", "test")
        m = merge_subagent_result("t-wrong", cr["subtask_id"], ws)
        assert m["ok"] is False


class TestProfileToolsFilter:
    def test_research_agent_is_read_only(self):
        p = get_profile("research_agent")
        assert "exec.run" not in p.allowed_tools
        assert not any("delete" in t for t in p.allowed_tools)

    def test_background_start_runs_persisted_task(self, monkeypatch):
        import time
        monkeypatch.setattr("agent.runtime.ssot_runtime.run_ssot_turn", _fake_run_turn)
        ws = f"ws_bg_{uuid.uuid4().hex[:8]}"
        cr = create_subagent_task("t1", ws, "s1", "research_agent", "Research")
        started = start_subagent_task(cr["subtask_id"], ws)
        assert started["ok"]
        deadline = time.time() + 2
        row = None
        while time.time() < deadline:
            row = next((x for x in list_subagent_tasks(ws) if x["subtask_id"] == cr["subtask_id"]), None)
            if row and row["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.02)
        assert row is not None
        assert row["status"] == "succeeded"
        rerun = run_subagent_task(cr["subtask_id"], ws)
        assert rerun["ok"] is False
        assert rerun["status"] == "succeeded"
        restarted = start_subagent_task(cr["subtask_id"], ws)
        assert restarted["ok"] is False
        assert restarted["status"] == "succeeded"

    def test_cancel_is_persisted_and_workspace_scoped(self):
        ws_a = f"ws_ca_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_cb_{uuid.uuid4().hex[:8]}"
        cr = create_subagent_task("t1", ws_a, "s1", "research_agent", "Research")
        assert cancel_subagent_task(cr["subtask_id"], ws_b)["ok"] is False
        assert cancel_subagent_task(cr["subtask_id"], ws_a)["ok"] is True
        rows = list_subagent_tasks(ws_a)
        assert next(x for x in rows if x["subtask_id"] == cr["subtask_id"])["status"] == "cancelled"
        assert all(x["subtask_id"] != cr["subtask_id"] for x in list_subagent_tasks(ws_b))
        assert cancel_subagent_task(cr["subtask_id"], ws_a)["ok"] is False

    def test_reconcile_marks_phantom_running_failed(self, monkeypatch, tmp_path):
        import storage.run_record_store as run_store

        ws = f"ws_restart_{uuid.uuid4().hex[:8]}"
        cr = create_subagent_task("t1", ws, "s1", "research_agent", "Research")
        from agent.runtime.durable.subagent import _load_task, _save_task
        task = _load_task(ws, cr["subtask_id"])
        task.status = "running"
        _save_task(task)
        assert reconcile_subagent_tasks() == [cr["subtask_id"]]
        row = list_subagent_tasks(ws)[0]
        assert row["status"] == "failed"
        assert row["summary"] == "Subagent interrupted by service restart"

    def test_query_loop_observes_cancel_callback(self):
        from core.runtime_engine.models import StatelessContext
        from core.runtime_engine.query_loop import QueryLoop

        ctx = StatelessContext(
            workspace_id="ws_cancel",
            session_id="s1",
            request_id="r1",
            user_input="diagnose",
            extras={"cancel_check": lambda: True},
        )
        assert QueryLoop._is_cancelled(ctx) is True

    def test_removed_development_profiles_are_absent(self):
        for profile_id in ("review_agent", "fix_agent", "test_agent", "doc_agent"):
            assert get_profile(profile_id) is None
