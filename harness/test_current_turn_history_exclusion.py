"""Regression: the accepted current user input must not be re-injected as history."""
from types import SimpleNamespace


def test_current_provisional_request_is_excluded_from_history_block(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.runtime.ssot_runtime import _build_history_block
    from storage.message_store import SessionMessageStore

    workspace_id = "ws-current-history"
    session_id = "session-current-history"
    user_input = "继续按刚才的网络变更计划执行下一步"
    store = SessionMessageStore(session_id=session_id, ws_id=workspace_id)
    store.write_message(
        "request_accepted_current",
        "user",
        user_input,
        metadata={"client_request_id": "request-current-history", "provisional": True},
    )
    session = SimpleNamespace(workspace_id=workspace_id, session_id=session_id, history=[])

    block = _build_history_block(
        session,
        user_input=user_input,
        max_tokens=800,
        exclude_client_request_id="request-current-history",
    )

    assert user_input not in block


def test_ssot_runtime_excludes_current_provisional_request_from_model_history(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.ssot_runtime import run_ssot_turn
    from storage.message_store import SessionMessageStore

    workspace_id = "ws-runtime-current-history"
    session_id = "session-runtime-current-history"
    request_id = "request-runtime-current-history"
    user_input = "继续按刚才的网络变更计划执行下一步"
    SessionMessageStore(session_id=session_id, ws_id=workspace_id).write_message(
        "request_accepted_current",
        "user",
        user_input,
        metadata={"client_request_id": request_id, "provisional": True},
    )
    captured = {}

    class FakeEngine:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                success=True,
                final_response="已继续。",
                tool_calls=[],
                events=[],
                errors=[],
                metadata={},
                node_results={},
            )

    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._build_engine",
        lambda **_kwargs: FakeEngine(),
    )
    monkeypatch.setattr(
        "agent.runtime.ssot_runtime.persist_run_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._record_experience_and_maybe_reflect",
        lambda **_kwargs: None,
    )

    session = AgentSession(session_id=session_id, workspace_id=workspace_id)
    turn = AgentTurn.from_op(AgentOp.user_message(
        user_input=user_input,
        session_id=session_id,
        workspace_id=workspace_id,
        metadata={"client_request_id": request_id},
    ))

    result = run_ssot_turn(session, turn)

    assert result.ok is True
    assert "conversation_history_block" not in captured["extras"]


def test_ssot_runtime_projects_and_commits_structured_task_continuation(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.ssot_runtime import run_ssot_turn
    from storage.message_store import SessionMessageStore
    from agent.runtime.task_continuation import load_task_continuation

    workspace_id = "ws-task-continuation"
    session_id = "session-task-continuation"
    store = SessionMessageStore(session_id=session_id, ws_id=workspace_id)
    seed_prompt = "连续输出4条数据中心网络交接检查项；每条必须以DC-开头、使用编号、每条一句完整中文。"
    seed_answer = "\n".join(
        f"DC-{index:02d}：数据中心网络交接检查项 {index}。"
        for index in range(1, 5)
    )
    store.write_message("run_1", "user", seed_prompt, metadata={})
    store.write_message("run_1", "assistant", seed_answer, metadata={})
    captured = {}

    class FakeEngine:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                success=True,
                final_response="DC-05：数据中心网络交接检查项 5。\nDC-06：数据中心网络交接检查项 6。",
                tool_calls=[],
                events=[],
                errors=[],
                metadata={},
                node_results={},
            )

    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._build_engine",
        lambda **_kwargs: FakeEngine(),
    )
    monkeypatch.setattr(
        "agent.runtime.ssot_runtime.persist_run_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._record_experience_and_maybe_reflect",
        lambda **_kwargs: None,
    )

    session = AgentSession(session_id=session_id, workspace_id=workspace_id)
    turn = AgentTurn.from_op(AgentOp.user_message(
        user_input="再来2条",
        session_id=session_id,
        workspace_id=workspace_id,
    ))
    result = run_ssot_turn(session, turn)

    contract = captured["extras"]["task_continuation_contract"]
    assert contract["validation"]["expected_new_items"] == 2
    assert contract["validation"]["expected_start_ordinal"] == 5
    trusted = captured["extras"]["trusted_prompt_items"]
    task_items = [item for item in trusted if item.source_kind == "task_continuation"]
    assert len(task_items) == 1
    assert seed_prompt not in task_items[0].content
    assert load_task_continuation(workspace_id, session_id)["active_task"]["delivery_contract"]["last_ordinal"] == 6
    assert result.ok is True
