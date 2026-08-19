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


def _fake_run_turn(session, turn, **kwargs):
    assert kwargs.get("requested_by") == "subagent"
    assert kwargs.get("allowed_tool_ids") is not None
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

    def test_research_agent_output_is_user_ready(self):
        contract = BUILTIN_PROFILES["research_agent"].output_contract
        assert "user-ready" in contract
        assert "bottom line" in contract
        assert "raw API" in contract

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

    def test_get_accepts_spawn_subtask_id_and_declares_tracking(self):
        from core.tools.general_tools.agent_tools import handle_agent_get_result
        from core.tools.schemas import ToolInvocation

        ws = f"ws_get_{uuid.uuid4().hex[:8]}"
        created = create_subagent_task(
            parent_task_id="t1", workspace_id=ws, session_id="s1",
            profile_id="research_agent", goal="Research",
        )
        result = handle_agent_get_result(ToolInvocation(
            tool_id="agent.manage",
            workspace_id=ws,
            arguments={"action": "get", "subtask_id": created["subtask_id"]},
        ))

        assert result["ok"] is True
        assert result["subtask_id"] == created["subtask_id"]
        assert "child_session_id" not in result
        assert result["tracking"]["task_id"] == created["subtask_id"]
        assert result["tracking"]["poll_arguments"]["subtask_id"] == created["subtask_id"]

    def test_background_spawn_returns_pollable_tracking(self, monkeypatch):
        from core.tools.general_tools.agent_tools import _run_durable_subagent

        monkeypatch.setattr(
            "agent.runtime.durable.subagent.start_subagent_task",
            lambda subtask_id, ws_id: {
                "ok": True, "subtask_id": subtask_id, "status": "running",
            },
        )
        ws = f"ws_track_{uuid.uuid4().hex[:8]}"
        result = _run_durable_subagent(
            instruction="Research", workspace_id=ws, session_id="s1",
            profile_id="research_agent", background=True,
        )

        assert result["ok"] is True
        assert result["status"] == "running"
        assert result["summary"]
        assert result["tracking"]["done"] is False
        assert result["tracking"]["poll_action"] == "get"
        assert result["tracking"]["suggested_next_action"] == "poll_get"

        from core.runtime_engine.query_loop import QueryLoop
        assert QueryLoop._should_poll_tracking("delegate this task", result["tracking"])


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

    def test_merge_rejects_unfinished_subtask(self):
        ws = f"ws_unfinished_{uuid.uuid4().hex[:8]}"
        cr = create_subagent_task("t-parent", ws, "s1", "research_agent", "Research")
        result = merge_subagent_result("t-parent", cr["subtask_id"], ws)
        assert result["ok"] is False
        assert result["status"] == "created"

    def test_get_missing_subtask_is_an_error(self):
        from core.tools.general_tools.agent_tools import handle_agent_get_result
        from core.tools.schemas import ToolInvocation

        result = handle_agent_get_result(ToolInvocation(
            tool_id="agent.manage", workspace_id=f"ws_missing_{uuid.uuid4().hex[:8]}",
            arguments={"action": "get", "subtask_id": "sub-00000000"},
        ))
        assert result["ok"] is False


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

    def test_reconcile_scans_principal_scoped_workspace_data(self, monkeypatch, tmp_path):
        from storage.principal import storage_principal
        from storage.workspace_store import ensure_workspace
        from agent.runtime.durable.subagent import _load_task, _save_task

        monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setenv("LZCORE_LOGIN_USERNAME", "Admin")
        ws = f"ws_principal_restart_{uuid.uuid4().hex[:8]}"
        with storage_principal("Admin"):
            ensure_workspace(ws)
            created = create_subagent_task("t1", ws, "s1", "research_agent", "Research")
            task = _load_task(ws, created["subtask_id"])
            task.status = "running"
            _save_task(task)

        assert reconcile_subagent_tasks() == [created["subtask_id"]]
        with storage_principal("Admin"):
            assert list_subagent_tasks(ws)[0]["status"] == "failed"

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


def test_tracking_exposes_only_latest_poll_but_keeps_full_events(monkeypatch):
    import asyncio
    from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
    from core.runtime_engine.query_loop import QueryLoop, StreamingToolResult

    class _Runtime:
        @staticmethod
        def has_tool(name):
            return name == "agent.manage"

    config = SSOTRuntimeConfig(
        tracking_max_polls=5,
        tracking_max_seconds=1,
        tracking_poll_interval_cap_seconds=0,
    )
    loop = QueryLoop(
        config,
        {"agent.manage": {"description": "", "args_schema": {"type": "object", "properties": {}}}},
        _Runtime(),
    )
    poll_number = 0

    async def _poll(call, **_kwargs):
        nonlocal poll_number
        poll_number += 1
        done = poll_number == 3
        status = "succeeded" if done else "running"
        return StreamingToolResult(
            tool_name="agent.manage",
            call_id=call.id,
            ok=True,
            output={
                "ok": True,
                "summary": f"Subagent status: {status}",
                "tracking": {
                    "kind": "long_task",
                    "task_id": "sub-1",
                    "status": status,
                    "done": done,
                    "suggested_next_action": "poll_get",
                    "poll_action": "get",
                    "poll_arguments": {"subtask_id": "sub-1"},
                },
            },
        )

    monkeypatch.setattr(loop._executor, "_execute_one", _poll)
    ctx = StatelessContext(
        workspace_id="default",
        session_id="s1",
        request_id="r1",
        user_input="delegate",
    )
    initial = StreamingToolResult(
        tool_name="agent.manage",
        call_id="spawn-1",
        ok=True,
        output={
            "ok": True,
            "tracking": {
                "kind": "long_task",
                "task_id": "sub-1",
                "status": "running",
                "done": False,
                "suggested_next_action": "poll_get",
                "poll_action": "get",
                "poll_arguments": {"subtask_id": "sub-1"},
            },
        },
    )

    exposed = asyncio.run(loop._settle_tracking(ctx, [initial]))

    assert poll_number == 3
    assert len(exposed) == 1
    assert exposed[0].call_id == "spawn-1_track_3"
    assert exposed[0].output["tracking_poll_count"] == 3
    assert len(ctx.extras["tracking_events"]) == 4


def test_agent_spawn_defaults_to_tracked_background(monkeypatch):
    import core.tools.general_tools.agent_tools as agent_tools
    from core.tools.schemas import ToolInvocation

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "running",
            "subtask_id": "sub-default-background",
            "profile_id": "research_agent",
        }

    monkeypatch.setattr(agent_tools, "_run_durable_subagent", fake_run)
    result = agent_tools.handle_agent_spawn(ToolInvocation(
        tool_id="agent.manage",
        workspace_id="default",
        session_id="session-default-background",
        arguments={"action": "spawn", "instruction": "查询天气并返回可验证结果"},
    ))

    assert result["ok"] is True
    assert captured["background"] is True


def test_agent_spawn_honors_explicit_foreground(monkeypatch):
    import core.tools.general_tools.agent_tools as agent_tools
    from core.tools.schemas import ToolInvocation

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "succeeded",
            "subtask_id": "sub-explicit-foreground",
            "profile_id": "research_agent",
        }

    monkeypatch.setattr(agent_tools, "_run_durable_subagent", fake_run)
    result = agent_tools.handle_agent_spawn(ToolInvocation(
        tool_id="agent.manage",
        workspace_id="default",
        session_id="session-explicit-foreground",
        arguments={
            "action": "spawn",
            "instruction": "执行需要同步完成的前台研究",
            "background": False,
        },
    ))

    assert result["ok"] is True
    assert captured["background"] is False


def test_completed_subagent_persists_full_result_artifact_and_get_result_exposes_it(monkeypatch):
    from artifacts.store import read_artifact_content
    from agent.runtime.durable.subagent import get_subagent_task
    from core.tools.general_tools.agent_tools import handle_agent_get_result
    from core.tools.schemas import ToolInvocation

    full_result = "子代理完整证据\n" + ("逐日天气数据\n" * 700)
    monkeypatch.setattr(
        "agent.runtime.ssot_runtime.run_ssot_turn",
        lambda *args, **kwargs: type("Result", (), {
            "ok": True,
            "final_response": full_result,
            "events": [],
            "tool_calls": [],
            "trace_id": "trace_complete_handoff",
        })(),
    )
    ws = f"ws_handoff_{uuid.uuid4().hex[:8]}"
    created = create_subagent_task("parent", ws, "s1", "research_agent", "Research")
    completed = run_subagent_task(created["subtask_id"], ws)
    assert completed["ok"] is True
    task = get_subagent_task(ws, created["subtask_id"])
    assert task["result_total_chars"] == len(full_result)
    assert task["result_artifact_id"]
    assert read_artifact_content(ws, task["result_artifact_id"]) == full_result

    handoff = handle_agent_get_result(ToolInvocation(
        tool_id="agent.manage",
        workspace_id=ws,
        arguments={"action": "get", "subtask_id": created["subtask_id"]},
    ))
    assert handoff["subagent_result_complete"] is True
    assert handoff["content_complete"] is True
    assert handoff["preview"] == full_result
    assert handoff["artifact_id"] == task["result_artifact_id"]


def test_query_loop_injects_complete_subagent_handoff_at_artifact_budget():
    import json
    from core.runtime_engine.models import SSOTRuntimeConfig
    from agent.llm.schemas import LLMToolCall
    from core.runtime_engine.query_loop import QueryLoop, StreamingToolResult

    full_result = "完整子代理证据" * 800
    loop = QueryLoop(
        SSOTRuntimeConfig(context_window_tokens=32000, max_input_tokens=16000),
        {"agent.manage": {"description": "", "args_schema": {"type": "object", "properties": {}}}},
        object(),
    )
    messages = loop._append_tool_round([], [LLMToolCall(id="call_handoff", name="agent.manage", arguments={})], [
        StreamingToolResult(
            tool_name="agent.manage",
            call_id="call_handoff",
            ok=True,
            output={
                "ok": True,
                "artifact_id": "art_child",
                "artifact_type": "output_data",
                "preview": full_result,
                "content_complete": True,
                "subagent_result_complete": True,
            },
        ),
    ])
    payload = json.loads(messages[-1].content)
    assert payload["content_complete"] is True
    assert payload["preview"] == full_result
    assert loop._has_complete_analysis_artifact([
        StreamingToolResult(
            tool_name="agent.manage", call_id="call_handoff", ok=True,
            output=payload,
        )
    ])


def test_query_loop_marks_oversized_subagent_handoff_incomplete():
    import json
    from core.runtime_engine.models import SSOTRuntimeConfig
    from agent.llm.schemas import LLMToolCall
    from core.runtime_engine.query_loop import QueryLoop, StreamingToolResult

    full_result = "完整子代理证据" * 8000
    loop = QueryLoop(
        SSOTRuntimeConfig(context_window_tokens=10000, max_input_tokens=2048, max_output_tokens=1024),
        {"agent.manage": {"description": "", "args_schema": {"type": "object", "properties": {}}}},
        object(),
    )
    messages = loop._append_tool_round([], [LLMToolCall(id="call_handoff", name="agent.manage", arguments={})], [
        StreamingToolResult(
            tool_name="agent.manage",
            call_id="call_handoff",
            ok=True,
            output={
                "ok": True,
                "artifact_id": "art_child",
                "artifact_type": "output_data",
                "preview": full_result,
                "content_complete": True,
                "subagent_result_complete": True,
            },
        ),
    ])
    payload = json.loads(messages[-1].content)
    assert payload["content_complete"] is False
    assert payload["content_returned_chars"] < len(full_result)
    assert payload["artifact_id"] == "art_child"
    # The control decision must follow the actual context projection rather
    # than the producer's original complete-content claim.
    assert loop._has_complete_analysis_artifact([
        StreamingToolResult(
            tool_name="agent.manage", call_id="call_handoff", ok=True,
            output={
                "ok": True,
                "artifact_id": "art_child",
                "artifact_type": "output_data",
                "preview": full_result,
                "content_complete": True,
                "subagent_result_complete": True,
            },
        )
    ]) is False
    assert loop._has_complete_analysis_artifact([
        StreamingToolResult(
            tool_name="workspace.artifact", call_id="call_artifact", ok=True,
            output={
                "ok": True,
                "artifact_id": "art_large",
                "artifact_type": "report",
                "preview": full_result,
                "content_complete": True,
            },
        )
    ]) is False
