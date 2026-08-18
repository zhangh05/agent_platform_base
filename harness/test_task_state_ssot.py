from __future__ import annotations


def _metadata(*, execution_outcome: str = "complete", assertion_status: str = "not_required", decision: str = "stop_completed") -> dict:
    return {
        "execution_outcome": execution_outcome,
        "goal_assertions": {
            "required": assertion_status != "not_required",
            "status": assertion_status,
            "failed": [] if assertion_status == "passed" else ["missing_evidence"],
        },
        "cognitive": {
            "outcome": decision,
            "blocking_unknown_count": 0,
        },
        "evidence": {"items": []},
    }


def _tool(*, call_id: str, ok: bool = True, tool_id: str = "web.manage") -> dict:
    return {
        "call_id": call_id,
        "tool_id": tool_id,
        "ok": ok,
        "summary": "official source fetched" if ok else "provider unavailable",
    }


def test_initial_task_state_is_evented_with_queryloop_facts(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import commit_task_state, list_task_events, load_task_state

    snapshot = commit_task_state(
        workspace_id="ws-task-state",
        session_id="session-task-state",
        run_id="run-initial",
        user_input="检索两个官方 RFC 页面并交叉验证元数据。",
        final_response="已使用官方页面完成交叉验证。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[_tool(call_id="call-a"), _tool(call_id="call-b")],
    )

    assert snapshot is not None
    assert snapshot["revision"] == 1
    task = snapshot["task"]
    assert task["status"] == "completed"
    assert task["source_run_id"] == "run-initial"
    assert len(task["nodes"]) == 2
    assert len(task["evidence_refs"]) == 2
    assert load_task_state("ws-task-state", "session-task-state") == snapshot
    events = list_task_events("ws-task-state", "session-task-state")
    assert len(events) == 1
    assert events[0]["event_type"] == "task_completed"
    assert events[0]["successful_tool_count"] == 2


def test_explicit_continuation_keeps_identity_and_increments_revision(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import commit_task_state, resolve_task_state

    workspace_id = "ws-resume"
    session_id = "session-resume"
    initial = commit_task_state(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id="run-one",
        user_input="检索两个官方 RFC 页面并交叉验证元数据。",
        final_response="第一阶段已完成。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[_tool(call_id="call-one")],
    )
    assert initial is not None
    messages = [
        {"role": "user", "content": "检索两个官方 RFC 页面并交叉验证元数据。", "run_id": "run-one"},
        {"role": "assistant", "content": "第一阶段已完成。", "run_id": "run-one"},
    ]

    contract = resolve_task_state(
        workspace_id=workspace_id,
        session_id=session_id,
        user_input="继续，补充正文级协议差异。",
        messages=messages,
    )
    assert contract is not None
    assert contract["task_id"] == initial["task"]["task_id"]
    assert contract["base_revision"] == 1
    assert contract["relationship"]["kind"] in {"resume", "repair", "expand", "refine"}

    resumed = commit_task_state(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id="run-two",
        user_input="继续，补充正文级协议差异。",
        final_response="正文级差异已补充。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[_tool(call_id="call-two")],
        continuation_contract=contract,
    )
    assert resumed is not None
    assert resumed["revision"] == 2
    assert resumed["task"]["task_id"] == initial["task"]["task_id"]
    assert resumed["task"]["source_run_id"] == "run-two"
    assert len(resumed["task"]["nodes"]) == 2


def test_new_topic_cannot_inherit_generic_task_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import commit_task_state, resolve_task_state

    commit_task_state(
        workspace_id="ws-isolation",
        session_id="session-isolation",
        run_id="run-one",
        user_input="检索两个官方 RFC 页面并交叉验证元数据。",
        final_response="第一阶段已完成。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[_tool(call_id="call-one")],
    )
    messages = [
        {"role": "user", "content": "检索两个官方 RFC 页面并交叉验证元数据。", "run_id": "run-one"},
        {"role": "assistant", "content": "第一阶段已完成。", "run_id": "run-one"},
    ]
    assert resolve_task_state(
        workspace_id="ws-isolation",
        session_id="session-isolation",
        user_input="分析一份新的交换机日志。",
        messages=messages,
    ) is None


def test_stale_continuation_compare_and_swap_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import commit_task_state, resolve_task_state

    initial = commit_task_state(
        workspace_id="ws-cas",
        session_id="session-cas",
        run_id="run-one",
        user_input="检索两个官方 RFC 页面并交叉验证元数据。",
        final_response="第一阶段已完成。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[_tool(call_id="call-one")],
    )
    assert initial is not None
    messages = [
        {"role": "user", "content": "检索两个官方 RFC 页面并交叉验证元数据。", "run_id": "run-one"},
        {"role": "assistant", "content": "第一阶段已完成。", "run_id": "run-one"},
    ]
    contract = resolve_task_state(
        workspace_id="ws-cas",
        session_id="session-cas",
        user_input="继续，补充正文级协议差异。",
        messages=messages,
    )
    assert contract is not None
    winner = commit_task_state(
        workspace_id="ws-cas",
        session_id="session-cas",
        run_id="run-two",
        user_input="继续，补充正文级协议差异。",
        final_response="已完成。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[_tool(call_id="call-two")],
        continuation_contract=contract,
    )
    assert winner is not None
    assert commit_task_state(
        workspace_id="ws-cas",
        session_id="session-cas",
        run_id="run-stale",
        user_input="继续，补充正文级协议差异。",
        final_response="过期写入。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[_tool(call_id="call-stale")],
        continuation_contract=contract,
    ) is None


def test_retryable_tool_failure_marks_replan_required(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import commit_task_state, list_task_events

    snapshot = commit_task_state(
        workspace_id="ws-replan",
        session_id="session-replan",
        run_id="run-one",
        user_input="读取两个官方页面并比较结果。",
        final_response="一个来源暂时不可用。",
        run_ok=True,
        runtime_metadata=_metadata(execution_outcome="partial", decision="continue_replan"),
        tool_calls=[_tool(call_id="call-ok"), _tool(call_id="call-failed", ok=False)],
    )
    assert snapshot is not None
    assert snapshot["task"]["status"] == "replan_required"
    assert snapshot["task"]["next_action"] == "propose_alternative_plan"
    assert snapshot["task"]["failure"]["classification"] == "tool_failure"
    assert list_task_events("ws-replan", "session-replan")[-1]["event_type"] == "replan_required"



def test_replan_contract_projects_failure_and_completed_mutation_fence(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import (
        commit_task_state,
        render_task_state_guidance,
        resolve_task_state,
    )

    first = commit_task_state(
        workspace_id="ws-replan-contract",
        session_id="session-replan-contract",
        run_id="run-one",
        user_input="收集两份官方资料并写入审计记录。",
        final_response="资料源暂时不可用，审计记录已写入。",
        run_ok=True,
        runtime_metadata={
            **_metadata(execution_outcome="partial", decision="continue_replan"),
            "task_state_execution_manifest": [
                {
                    "tool_id": "workspace.file",
                    "call_key": 'workspace.file:{"arguments":{"action":"write","path":"audit.md"},"result_bindings":{}}',
                    "side_effecting": True,
                    "ok": True,
                },
            ],
        },
        tool_calls=[_tool(call_id="write-audit", tool_id="workspace.file"), _tool(call_id="read-source", ok=False)],
    )
    assert first is not None
    assert first["task"]["status"] == "replan_required"
    messages = [
        {"role": "user", "content": "收集两份官方资料并写入审计记录。", "run_id": "run-one"},
        {"role": "assistant", "content": "资料源暂时不可用，审计记录已写入。", "run_id": "run-one"},
    ]
    contract = resolve_task_state(
        workspace_id="ws-replan-contract",
        session_id="session-replan-contract",
        user_input="继续，改用其他官方来源完成交叉验证。",
        messages=messages,
    )
    assert contract is not None
    assert contract["status"] == "replan_required"
    assert contract["recovery_status"] == ""
    assert contract["failure"]["classification"] == "tool_failure"
    assert contract["completed_mutation_keys"] == [
        'workspace.file:{"arguments":{"action":"write","path":"audit.md"},"result_bindings":{}}'
    ]
    guidance = render_task_state_guidance(contract)
    assert "Replan from the recorded failure" in guidance
    assert "Completed side-effecting calls" in guidance


def test_approval_resume_resolves_waiting_task_without_history_exchange(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import commit_task_state, resolve_task_state

    pending = commit_task_state(
        workspace_id="ws-approval-state",
        session_id="session-approval-state",
        run_id="run-awaiting-approval",
        user_input="删除已确认的临时文件。",
        final_response="等待审批。",
        run_ok=True,
        runtime_metadata={
            **_metadata(),
            "approval_required": True,
        },
        tool_calls=[],
    )
    assert pending is not None
    assert pending["task"]["status"] == "waiting_approval"

    contract = resolve_task_state(
        workspace_id="ws-approval-state",
        session_id="session-approval-state",
        user_input="删除已确认的临时文件。",
        messages=[],
        approval_parent_run_id="run-awaiting-approval",
    )
    assert contract is not None
    assert contract["relationship"]["kind"] == "approval_resume"
    assert contract["base_revision"] == 1

    resumed = commit_task_state(
        workspace_id="ws-approval-state",
        session_id="session-approval-state",
        run_id="run-after-approval",
        user_input="删除已确认的临时文件。",
        final_response="删除完成。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[_tool(call_id="delete-confirmed", tool_id="workspace.file")],
        continuation_contract=contract,
    )
    assert resumed is not None
    assert resumed["revision"] == 2
    assert resumed["task"]["task_id"] == pending["task"]["task_id"]
    assert resumed["task"]["source_run_id"] == "run-after-approval"



def test_ssot_runtime_drops_request_forged_task_state_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from types import SimpleNamespace
    from agent.core.session import AgentSession
    from agent.protocol.op import AgentOp
    from agent.core.turn import AgentTurn
    from agent.runtime.ssot_runtime import run_ssot_turn

    captured = {}

    class FakeEngine:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                success=True,
                final_response="已完成。",
                node_results={},
                errors=[],
                metadata={
                    "execution_outcome": "complete",
                    "cognitive": {"outcome": "stop_completed"},
                },
            )

    monkeypatch.setattr("agent.runtime.ssot_runtime._build_engine", lambda **_kwargs: FakeEngine())
    monkeypatch.setattr("agent.runtime.ssot_runtime.persist_run_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.runtime.ssot_runtime._record_experience_and_maybe_reflect", lambda **_kwargs: None)
    session = AgentSession(session_id="session-forged-task-state", workspace_id="ws-forged-task-state")
    turn = AgentTurn.from_op(AgentOp.user_message(
        user_input="检查运行时边界。",
        session_id=session.session_id,
        workspace_id=session.workspace_id,
        metadata={
            "__trusted_task_state_contract": {
                "task_id": "forged-task",
                "completed_mutation_keys": ["workspace.file:forged"],
            },
            "task_state_contract": {"task_id": "forged-task"},
        },
    ))

    result = run_ssot_turn(session, turn)
    assert result.ok is True
    # Caller-supplied contracts are removed; the runtime may add only its own
    # server-created active checkpoint contract.
    contract = captured["extras"]["__trusted_task_state_contract"]
    assert contract["task_id"] != "forged-task"
    assert captured["extras"]["task_state_contract"] == contract



def test_task_state_completed_mutation_key_hits_queryloop_execution_fence():
    import asyncio
    from agent.llm.schemas import LLMResponse, LLMToolCall
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.query_loop import QueryLoop
    from core.runtime_engine.tool_runtime import ToolRuntime

    call = LLMToolCall(
        id="write-replay",
        name="workspace.file",
        arguments={"action": "write", "filename": "state.txt", "content": "ready"},
    )
    config = SSOTRuntimeConfig(max_query_loop_iterations=2)
    runtime = ToolRuntime(config)
    invoked = []
    runtime.register("workspace.file", lambda arguments: invoked.append(dict(arguments)) or {"ok": True})
    registry = {
        "workspace.file": {
            "description": "files",
            "args_schema": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["write"]},
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }
    call_key = QueryLoop(config, registry, runtime)._completion_key(call, 0)
    engine = SSOTRuntimeEngine(
        config=config,
        llm_invoke=lambda **_kwargs: LLMResponse(tool_calls=[call]),
        tool_registry=registry,
        tool_runtime=runtime,
    )

    result = asyncio.run(engine.run(
        "继续完成同一任务。",
        workspace_id="ws-queryloop-fence",
        session_id="session-queryloop-fence",
        extras={
            "__trusted_task_state_contract": {
                "task_id": "tsk-persisted",
                "completed_mutation_keys": [call_key],
            },
        },
    ))
    assert invoked == []
    assert result.success is False
    assert "duplicate_mutation_call" in result.errors



def test_second_consecutive_replan_failure_converges_to_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import commit_task_state, list_task_events, resolve_task_state

    first = commit_task_state(
        workspace_id="ws-replan-budget",
        session_id="session-replan-budget",
        run_id="run-one",
        user_input="收集两份官方资料并比对。",
        final_response="首个来源不可用。",
        run_ok=True,
        runtime_metadata=_metadata(execution_outcome="partial", decision="continue_replan"),
        tool_calls=[_tool(call_id="source-one", ok=False)],
    )
    assert first is not None
    assert first["task"]["status"] == "replan_required"
    assert first["task"]["replan_attempts"] == 1
    contract = resolve_task_state(
        workspace_id="ws-replan-budget",
        session_id="session-replan-budget",
        user_input="继续，改用另一个官方来源。",
        messages=[
            {"role": "user", "content": "收集两份官方资料并比对。", "run_id": "run-one"},
            {"role": "assistant", "content": "首个来源不可用。", "run_id": "run-one"},
        ],
    )
    assert contract is not None
    second = commit_task_state(
        workspace_id="ws-replan-budget",
        session_id="session-replan-budget",
        run_id="run-two",
        user_input="继续，改用另一个官方来源。",
        final_response="替代来源仍不可用。",
        run_ok=True,
        runtime_metadata=_metadata(execution_outcome="partial", decision="continue_replan"),
        tool_calls=[_tool(call_id="source-two", ok=False)],
        continuation_contract=contract,
    )
    assert second is not None
    assert second["task"]["replan_attempts"] == 2
    assert second["task"]["status"] == "failed"
    assert second["task"]["next_action"] == "replan_budget_exhausted"
    assert list_task_events("ws-replan-budget", "session-replan-budget")[-1]["event_type"] == "task_failed"



def test_ssot_approval_pending_commits_waiting_task_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from types import SimpleNamespace
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.ssot_runtime import run_ssot_turn

    class FakeEngine:
        async def run(self, **_kwargs):
            return SimpleNamespace(
                success=True,
                final_response="该操作等待审批。",
                node_results={},
                errors=[],
                metadata={
                    "approval_required": True,
                    "execution_outcome": "complete",
                    "cognitive": {"outcome": "stop_waiting_approval"},
                },
            )

    monkeypatch.setattr("agent.runtime.ssot_runtime._build_engine", lambda **_kwargs: FakeEngine())
    monkeypatch.setattr("agent.runtime.ssot_runtime.persist_run_record", lambda *_args, **_kwargs: None)
    session = AgentSession(session_id="session-approval-pending", workspace_id="ws-approval-pending")
    turn = AgentTurn.from_op(AgentOp.user_message(
        user_input="删除已确认的临时文件。",
        session_id=session.session_id,
        workspace_id=session.workspace_id,
    ))
    result = run_ssot_turn(session, turn)
    assert result.ok is True
    assert result.metadata["task_state"]["task"]["status"] == "waiting_approval"
    assert result.metadata["task_state"]["task"]["source_run_id"] == turn.turn_id


def test_ssot_approval_resume_reuses_waiting_task_state_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from types import SimpleNamespace
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.ssot_runtime import run_ssot_turn
    from agent.runtime.task_state import commit_task_state

    pending = commit_task_state(
        workspace_id="ws-approval-resume-runtime",
        session_id="session-approval-resume-runtime",
        run_id="run-pending",
        user_input="删除已确认的临时文件。",
        final_response="等待审批。",
        run_ok=True,
        runtime_metadata={**_metadata(), "approval_required": True},
        tool_calls=[],
    )
    assert pending is not None
    captured = {}

    class FakeEngine:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                success=True,
                final_response="删除完成。",
                node_results={},
                errors=[],
                metadata={
                    "execution_outcome": "complete",
                    "cognitive": {"outcome": "stop_completed"},
                },
            )

    monkeypatch.setattr("agent.runtime.ssot_runtime._build_engine", lambda **_kwargs: FakeEngine())
    monkeypatch.setattr("agent.runtime.ssot_runtime.persist_run_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.runtime.ssot_runtime._record_experience_and_maybe_reflect", lambda **_kwargs: None)
    session = AgentSession(session_id="session-approval-resume-runtime", workspace_id="ws-approval-resume-runtime")
    turn = AgentTurn.from_op(AgentOp.user_message(
        user_input="删除已确认的临时文件。",
        session_id=session.session_id,
        workspace_id=session.workspace_id,
        metadata={
            "__approval_continuation_resume": True,
            "approval_parent_run_id": "run-pending",
        },
    ))
    result = run_ssot_turn(session, turn)
    assert captured["extras"]["task_state_contract"]["relationship"]["kind"] == "approval_resume"
    assert captured["extras"]["task_state_contract"]["status"] == "active"
    assert captured["extras"]["task_state_contract"]["recovery_status"] == "waiting_approval"
    assert result.metadata["task_state"]["revision"] == 3
    assert result.metadata["task_state"]["task"]["task_id"] == pending["task"]["task_id"]
    assert result.metadata["task_state"]["task"]["status"] == "completed"



def test_ssot_runtime_projects_replan_contract_on_next_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from types import SimpleNamespace
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.ssot_runtime import run_ssot_turn

    calls = []

    class FakeEngine:
        async def run(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(
                    success=True,
                    final_response="首个来源不可用，等待替代规划。",
                    node_results={},
                    errors=[],
                    metadata={
                        "execution_outcome": "partial",
                        "cognitive": {"outcome": "continue_replan"},
                    },
                )
            return SimpleNamespace(
                success=True,
                final_response="已使用替代来源完成。",
                node_results={},
                errors=[],
                metadata={
                    "execution_outcome": "complete",
                    "cognitive": {"outcome": "stop_completed"},
                },
            )

    monkeypatch.setattr("agent.runtime.ssot_runtime._build_engine", lambda **_kwargs: FakeEngine())
    monkeypatch.setattr("agent.runtime.ssot_runtime.persist_run_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.runtime.ssot_runtime._record_experience_and_maybe_reflect", lambda **_kwargs: None)
    session = AgentSession(session_id="session-runtime-replan", workspace_id="ws-runtime-replan")
    initial_turn = AgentTurn.from_op(AgentOp.user_message(
        user_input="读取两个官方来源并交叉验证。",
        session_id=session.session_id,
        workspace_id=session.workspace_id,
    ))
    initial = run_ssot_turn(session, initial_turn)
    assert initial.metadata["task_state"]["task"]["status"] == "replan_required"

    resume_turn = AgentTurn.from_op(AgentOp.user_message(
        user_input="继续，改用替代官方来源。",
        session_id=session.session_id,
        workspace_id=session.workspace_id,
    ))
    resumed = run_ssot_turn(session, resume_turn)
    contract = calls[1]["extras"]["task_state_contract"]
    assert contract["task_id"] == initial.metadata["task_state"]["task"]["task_id"]
    assert contract["status"] == "active"
    assert contract["recovery_status"] == "replan_required"
    assert contract["next_action"] == "run_query_loop"
    trusted_items = calls[1]["extras"]["trusted_prompt_items"]
    task_state_items = [item for item in trusted_items if item.source_kind == "task_state"]
    assert len(task_state_items) == 1
    assert "recovery_status=replan_required" in task_state_items[0].content
    assert "next_action=run_query_loop" in task_state_items[0].content
    assert resumed.metadata["task_state"]["revision"] == 4
    assert resumed.metadata["task_state"]["task"]["status"] == "completed"



def test_persisted_task_state_recovers_identity_after_runtime_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import commit_task_state, load_task_state, resolve_task_state

    committed = commit_task_state(
        workspace_id="ws-restart-recovery",
        session_id="session-restart-recovery",
        run_id="run-before-restart",
        user_input="检索两个官方 RFC 页面并交叉验证元数据。",
        final_response="第一阶段已完成。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[_tool(call_id="call-before-restart")],
    )
    assert committed is not None
    # The resolver reads task_state.json under a file lock; no process-local
    # cache participates in recovery.
    recovered = load_task_state("ws-restart-recovery", "session-restart-recovery")
    assert recovered["revision"] == 1
    assert recovered["task"]["task_id"] == committed["task"]["task_id"]
    contract = resolve_task_state(
        workspace_id="ws-restart-recovery",
        session_id="session-restart-recovery",
        user_input="继续，补充正文级协议差异。",
        messages=[
            {"role": "user", "content": "检索两个官方 RFC 页面并交叉验证元数据。", "run_id": "run-before-restart"},
            {"role": "assistant", "content": "第一阶段已完成。", "run_id": "run-before-restart"},
        ],
    )
    assert contract is not None
    assert contract["task_id"] == committed["task"]["task_id"]
    assert contract["base_revision"] == recovered["revision"]

def _install_task_state_failure_probe_engine(monkeypatch):
    from types import SimpleNamespace

    class FakeEngine:
        async def run(self, **_kwargs):
            return SimpleNamespace(
                success=True,
                final_response="模型生成了表面成功回复。",
                node_results={},
                errors=[],
                metadata={
                    "execution_outcome": "complete",
                    "cognitive": {"outcome": "stop_completed"},
                },
            )

    monkeypatch.setattr("agent.runtime.ssot_runtime._build_engine", lambda **_kwargs: FakeEngine())
    monkeypatch.setattr("agent.runtime.ssot_runtime.persist_run_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.runtime.ssot_runtime._record_experience_and_maybe_reflect", lambda **_kwargs: None)


def test_ssot_task_state_commit_failure_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.ssot_runtime import run_ssot_turn

    _install_task_state_failure_probe_engine(monkeypatch)
    monkeypatch.setattr(
        "agent.runtime.task_state.commit_task_state",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("simulated state storage failure")),
    )
    session = AgentSession(session_id="session-task-state-commit-failure", workspace_id="ws-task-state-failure")
    result = run_ssot_turn(session, AgentTurn.from_op(AgentOp.user_message(
        user_input="检索一份官方资料。",
        session_id=session.session_id,
        workspace_id=session.workspace_id,
    )))

    assert result.ok is False
    assert result.error_type == "task_state_commit_failed"
    assert "task_state_commit_failed" in result.errors
    assert result.metadata["task_state_persistence"]["stage"] == "commit"


def test_ssot_task_state_resolution_failure_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.ssot_runtime import run_ssot_turn

    called = []
    _install_task_state_failure_probe_engine(monkeypatch)
    monkeypatch.setattr("agent.runtime.ssot_runtime._build_engine", lambda **_kwargs: called.append(True))
    monkeypatch.setattr(
        "agent.runtime.task_state.resolve_task_state",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("simulated state read failure")),
    )
    session = AgentSession(session_id="session-task-state-resolution-failure", workspace_id="ws-task-state-failure")
    result = run_ssot_turn(session, AgentTurn.from_op(AgentOp.user_message(
        user_input="继续，补充验证。",
        session_id=session.session_id,
        workspace_id=session.workspace_id,
    )))

    assert result.ok is False
    assert result.error_type == "task_state_resolution_failed"
    assert "task_state_resolution_failed" in result.errors
    assert result.metadata["task_state_persistence"]["stage"] == "resolution"
    assert called == []

def test_replan_contract_persists_failed_call_keys_and_queryloop_rejects_replay(monkeypatch, tmp_path):
    import asyncio
    from agent.llm.schemas import LLMResponse, LLMToolCall
    from agent.runtime.task_state import commit_task_state, resolve_task_state
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.query_loop import QueryLoop
    from core.runtime_engine.tool_runtime import ToolRuntime

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    call = LLMToolCall(
        id="failed-replay",
        name="workspace.file",
        arguments={"action": "write", "filename": "state.txt", "content": "ready"},
    )
    config = SSOTRuntimeConfig(max_query_loop_iterations=2)
    runtime = ToolRuntime(config)
    invoked = []
    runtime.register("workspace.file", lambda arguments: invoked.append(dict(arguments)) or {"ok": True})
    registry = {
        "workspace.file": {
            "description": "files",
            "args_schema": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["write"]},
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }
    call_key = QueryLoop(config, registry, runtime)._completion_key(call, 0)
    first = commit_task_state(
        workspace_id="ws-replan-fence",
        session_id="session-replan-fence",
        run_id="run-one",
        user_input="写入状态文件。",
        final_response="首次调用失败。",
        run_ok=False,
        runtime_metadata={
            **_metadata(execution_outcome="partial", decision="continue_replan"),
            "task_state_execution_manifest": [
                {"tool_id": "workspace.file", "call_key": call_key, "side_effecting": True, "ok": False}
            ],
        },
        tool_calls=[_tool(call_id="failed-replay", ok=False, tool_id="workspace.file")],
    )
    assert first is not None
    contract = resolve_task_state(
        workspace_id="ws-replan-fence",
        session_id="session-replan-fence",
        user_input="继续，换一种方式恢复。",
        messages=[
            {"role": "user", "content": "写入状态文件。", "run_id": "run-one"},
            {"role": "assistant", "content": "首次调用失败。", "run_id": "run-one"},
        ],
    )
    assert contract is not None
    assert contract["status"] == "replan_required"
    assert contract["failed_replan_call_keys"] == [call_key]

    engine = SSOTRuntimeEngine(
        config=config,
        llm_invoke=lambda **_kwargs: LLMResponse(tool_calls=[call]),
        tool_registry=registry,
        tool_runtime=runtime,
    )
    result = asyncio.run(engine.run(
        "继续，换一种方式恢复。",
        workspace_id="ws-replan-fence",
        session_id="session-replan-fence",
        extras={"__trusted_task_state_contract": contract},
    ))
    assert invoked == []
    assert result.success is False
    assert "replan_repeated_failed_call" in result.errors
    assert result.metadata["cognitive"]["outcome"] == "continue_replan"

def test_task_state_maps_server_cancel_fact_to_cancelled(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import commit_task_state, list_task_events

    snapshot = commit_task_state(
        workspace_id="ws-task-cancelled",
        session_id="session-task-cancelled",
        run_id="run-cancelled",
        user_input="执行前停止任务。",
        final_response="任务已取消。",
        run_ok=False,
        runtime_metadata={
            **_metadata(execution_outcome="failed", decision="stop_failed"),
            "runtime_errors": ["cancelled_by_user"],
        },
        tool_calls=[],
    )
    assert snapshot is not None
    assert snapshot["task"]["status"] == "cancelled"
    assert snapshot["task"]["next_action"] == "cancelled_by_user"
    assert list_task_events("ws-task-cancelled", "session-task-cancelled")[-1]["event_type"] == "task_cancelled"

def test_begin_task_state_creates_active_checkpoint_and_terminal_commit_advances_revision(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import begin_task_state, commit_task_state, list_task_events

    active = begin_task_state(
        workspace_id="ws-active-checkpoint",
        session_id="session-active-checkpoint",
        run_id="run-active",
        user_input="检索两份官方资料。",
    )
    assert active is not None
    assert active["status"] == "active"
    assert active["base_revision"] == 1
    terminal = commit_task_state(
        workspace_id="ws-active-checkpoint",
        session_id="session-active-checkpoint",
        run_id="run-active",
        user_input="检索两份官方资料。",
        final_response="已完成。",
        run_ok=True,
        runtime_metadata=_metadata(),
        tool_calls=[],
        continuation_contract=active,
    )
    assert terminal is not None
    assert terminal["revision"] == 2
    assert terminal["task"]["status"] == "completed"
    assert [item["event_type"] for item in list_task_events("ws-active-checkpoint", "session-active-checkpoint")] == ["task_started", "task_completed"]


def test_startup_reconciliation_interrupts_active_task_and_explicit_resume_recovers_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import begin_task_state, reconcile_active_task_states, resolve_task_state

    active = begin_task_state(
        workspace_id="ws-interrupt-recovery",
        session_id="session-interrupt-recovery",
        run_id="run-interrupt",
        user_input="检索两份官方资料。",
    )
    assert active is not None
    outcome = reconcile_active_task_states("ws-interrupt-recovery")
    assert outcome["interrupted"] == 1
    recovered = resolve_task_state(
        workspace_id="ws-interrupt-recovery",
        session_id="session-interrupt-recovery",
        user_input="继续",
        messages=[{"role": "user", "content": "检索两份官方资料。", "run_id": "run-interrupt"}],
    )
    assert recovered is not None
    assert recovered["status"] == "interrupted"
    assert recovered["source_run_id"] == "run-interrupt"


def test_ssot_runtime_persists_active_checkpoint_before_engine_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from types import SimpleNamespace
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.task_state import load_task_state
    from agent.runtime.ssot_runtime import run_ssot_turn

    captured = {}
    class FakeEngine:
        async def run(self, **kwargs):
            captured.update(kwargs)
            state = load_task_state("ws-runtime-active", "session-runtime-active")
            assert state["task"]["status"] == "active"
            assert state["task"]["source_run_id"]
            return SimpleNamespace(
                success=True,
                final_response="已完成。",
                node_results={},
                errors=[],
                metadata={"execution_outcome": "complete", "cognitive": {"outcome": "stop_completed"}},
            )

    monkeypatch.setattr("agent.runtime.ssot_runtime._build_engine", lambda **_kwargs: FakeEngine())
    monkeypatch.setattr("agent.runtime.ssot_runtime.persist_run_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.runtime.ssot_runtime._record_experience_and_maybe_reflect", lambda **_kwargs: None)
    session = AgentSession(session_id="session-runtime-active", workspace_id="ws-runtime-active")
    result = run_ssot_turn(session, AgentTurn.from_op(AgentOp.user_message(
        user_input="检索两份官方资料。",
        session_id=session.session_id,
        workspace_id=session.workspace_id,
    )))
    assert result.ok is True
    assert result.metadata["task_state"]["task"]["status"] == "completed"
    assert captured["extras"]["__trusted_task_state_contract"]["status"] == "active"

def test_task_state_tool_checkpoint_preserves_unknown_mutation_for_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.task_state import (
        begin_task_state,
        checkpoint_task_state_execution,
        reconcile_active_task_states,
        load_task_state,
    )

    active = begin_task_state(
        workspace_id="ws-tool-checkpoint",
        session_id="session-tool-checkpoint",
        run_id="run-tool-checkpoint",
        user_input="写入审计文件。",
    )
    assert active is not None
    prepared = checkpoint_task_state_execution(
        workspace_id="ws-tool-checkpoint",
        session_id="session-tool-checkpoint",
        run_id="run-tool-checkpoint",
        contract=active,
        phase="prepared",
        manifest=[{
            "tool_id": "workspace.file",
            "call_key": "workspace.file:write:audit",
            "side_effecting": True,
        }],
    )
    assert prepared is not None
    assert prepared["pending_mutation_keys"] == ["workspace.file:write:audit"]
    outcome = reconcile_active_task_states("ws-tool-checkpoint")
    assert outcome["interrupted"] == 1
    recovered = load_task_state("ws-tool-checkpoint", "session-tool-checkpoint")
    assert recovered["task"]["status"] == "waiting_user"
    assert recovered["task"]["next_action"] == "resolve_unknown_mutation_outcome"

def test_queryloop_unknown_mutation_checkpoint_freezes_new_writes():
    import asyncio
    from agent.llm.schemas import LLMResponse, LLMToolCall
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.tool_runtime import ToolRuntime

    call = LLMToolCall(
        id="unknown-write",
        name="workspace.file",
        arguments={"action": "write", "filename": "audit.txt", "content": "new"},
    )
    config = SSOTRuntimeConfig(max_query_loop_iterations=2)
    runtime = ToolRuntime(config)
    invoked = []
    runtime.register("workspace.file", lambda arguments: invoked.append(dict(arguments)) or {"ok": True})
    registry = {
        "workspace.file": {
            "description": "files",
            "args_schema": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["write"]},
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }
    engine = SSOTRuntimeEngine(
        config=config,
        llm_invoke=lambda **_kwargs: LLMResponse(tool_calls=[call]),
        tool_registry=registry,
        tool_runtime=runtime,
    )
    result = asyncio.run(engine.run(
        "继续完成任务。",
        workspace_id="ws-unknown-mutation",
        session_id="session-unknown-mutation",
        extras={
            "__trusted_task_state_contract": {
                "task_id": "tsk-unknown",
                "status": "waiting_user",
                "pending_mutation_keys": ["workspace.file:write:unknown"],
            },
        },
    ))
    assert invoked == []
    assert result.success is False
    assert "task_state_unknown_mutation_outcome" in result.errors


def test_queryloop_checkpoint_failure_prevents_tool_execution():
    import asyncio
    from agent.llm.schemas import LLMResponse, LLMToolCall
    from core.runtime_engine.engine import SSOTRuntimeEngine
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.tool_runtime import ToolRuntime

    call = LLMToolCall(
        id="checkpoint-write",
        name="workspace.file",
        arguments={"action": "write", "filename": "audit.txt", "content": "new"},
    )
    config = SSOTRuntimeConfig(max_query_loop_iterations=2)
    runtime = ToolRuntime(config)
    invoked = []
    runtime.register("workspace.file", lambda arguments: invoked.append(dict(arguments)) or {"ok": True})
    registry = {
        "workspace.file": {
            "description": "files",
            "args_schema": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": ["write"]},
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }
    engine = SSOTRuntimeEngine(
        config=config,
        llm_invoke=lambda **_kwargs: LLMResponse(tool_calls=[call]),
        tool_registry=registry,
        tool_runtime=runtime,
    )
    result = asyncio.run(engine.run(
        "写入审计文件。",
        workspace_id="ws-checkpoint-failure",
        session_id="session-checkpoint-failure",
        extras={
            "__task_state_execution_checkpoint": lambda *_args: (_ for _ in ()).throw(OSError("simulated checkpoint failure")),
        },
    ))
    assert invoked == []
    assert result.success is False
    assert "task_state_checkpoint_failed" in result.errors

def test_task_state_commit_failure_does_not_advance_secondary_continuation(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.ssot_runtime import run_ssot_turn

    _install_task_state_failure_probe_engine(monkeypatch)
    continuation_calls = []
    monkeypatch.setattr(
        "agent.runtime.task_state.commit_task_state",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("simulated state storage failure")),
    )
    monkeypatch.setattr(
        "agent.runtime.task_continuation.commit_task_continuation",
        lambda **_kwargs: continuation_calls.append(True) or {},
    )
    session = AgentSession(session_id="session-secondary-order", workspace_id="ws-secondary-order")
    result = run_ssot_turn(session, AgentTurn.from_op(AgentOp.user_message(
        user_input="检索一份官方资料。",
        session_id=session.session_id,
        workspace_id=session.workspace_id,
    )))
    assert result.ok is False
    assert result.error_type == "task_state_commit_failed"
    assert continuation_calls == []
