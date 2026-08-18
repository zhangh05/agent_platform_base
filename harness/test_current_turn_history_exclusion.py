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


def test_ssot_runtime_continuation_uses_assistant_terminal_run_id_from_persisted_history(monkeypatch, tmp_path):
    """Production request rows and final assistant rows intentionally have different run IDs."""
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.core.session import AgentSession
    from agent.core.turn import AgentTurn
    from agent.protocol.op import AgentOp
    from agent.runtime.ssot_runtime import _load_context_messages, run_ssot_turn
    from agent.runtime.task_continuation import commit_task_continuation, load_task_continuation
    from storage.message_store import SessionMessageStore

    workspace_id = "ws-production-shaped-continuation"
    session_id = "session-production-shaped-continuation"
    seed_request_id = "request-accepted-seed"
    seed_final_run_id = "run-seed-final"
    seed_prompt = "连续输出4条数据中心网络交接检查项；每条必须以DC-开头、使用编号、每条一句完整中文。"
    seed_answer = "\n".join(
        f"DC-{index:02d}：数据中心网络交接检查项 {index}。"
        for index in range(1, 5)
    )
    store = SessionMessageStore(session_id=session_id, ws_id=workspace_id)
    store.write_message(seed_request_id, "user", seed_prompt, metadata={"client_request_id": "client-seed", "provisional": True})
    store.write_message(seed_final_run_id, "assistant", seed_answer, metadata={})
    assert commit_task_continuation(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=seed_final_run_id,
        user_input=seed_prompt,
        assistant_response=seed_answer,
        run_ok=True,
    ) is not None
    persisted = _load_context_messages(AgentSession(session_id=session_id, workspace_id=workspace_id))
    assert [message["run_id"] for message in persisted[-2:]] == [seed_request_id, seed_final_run_id]

    captured = {}
    class FakeEngine:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                success=True,
                final_response="DC-05：数据中心网络交接检查项 5。\nDC-06：数据中心网络交接检查项 6。",
                tool_calls=[], events=[], errors=[], metadata={}, node_results={},
            )
    monkeypatch.setattr("agent.runtime.ssot_runtime._build_engine", lambda **_kwargs: FakeEngine())
    monkeypatch.setattr("agent.runtime.ssot_runtime.persist_run_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.runtime.ssot_runtime._record_experience_and_maybe_reflect", lambda **_kwargs: None)

    session = AgentSession(session_id=session_id, workspace_id=workspace_id)
    turn = AgentTurn.from_op(AgentOp.user_message(user_input="再来2条", session_id=session_id, workspace_id=workspace_id))
    result = run_ssot_turn(session, turn)

    contract = captured["extras"]["task_continuation_contract"]
    assert contract["bootstrap"] is False
    assert contract["source_run_id"] == seed_final_run_id
    assert contract["validation"]["expected_start_ordinal"] == 5
    assert load_task_continuation(workspace_id, session_id)["active_task"]["delivery_contract"]["last_ordinal"] == 6
    assert result.ok is True


def test_context_projection_excludes_current_prewrite_memory_tail(monkeypatch, tmp_path):
    """The current provisional request must not break the prior complete exchange."""
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.core.session import AgentSession
    from agent.protocol.message import AssistantMessage, UserMessage
    from agent.runtime.ssot_runtime import _load_context_messages
    from agent.runtime.task_continuation import commit_task_continuation, resolve_task_continuation
    from storage.message_store import SessionMessageStore

    workspace_id = "ws-current-prewrite-memory"
    session_id = "session-current-prewrite-memory"
    request_id = "request-current-scope"
    seed_prompt = "连续输出4条数据中心网络交接检查项；每条必须以DC-开头、使用编号、每条一句完整中文。"
    seed_answer = "\n".join(
        f"DC-{index:02d}：数据中心网络交接检查项 {index}。"
        for index in range(1, 5)
    )
    scope_input = "删除其他章节，只保留3条，并保持 DC- 前缀和连续编号。"
    store = SessionMessageStore(session_id=session_id, ws_id=workspace_id)
    store.write_message("request_seed", "user", seed_prompt, metadata={})
    store.write_message("run_seed", "assistant", seed_answer, metadata={})
    store.write_message(
        "request_scope", "user", scope_input,
        metadata={"client_request_id": request_id, "provisional": True},
    )
    assert commit_task_continuation(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id="run_seed",
        user_input=seed_prompt,
        assistant_response=seed_answer,
        run_ok=True,
    ) is not None

    session = AgentSession(
        session_id=session_id,
        workspace_id=workspace_id,
        history=[
            UserMessage(content=seed_prompt, message_id="request_seed:user", run_id="request_seed"),
            AssistantMessage(content=seed_answer, message_id="run_seed:assistant", run_id="run_seed"),
            UserMessage(content=scope_input, message_id="request_scope:user", client_request_id=request_id),
        ],
    )
    messages = _load_context_messages(
        session,
        exclude_client_request_id=request_id,
    )
    assert [(message["role"], message["content"], message.get("run_id")) for message in messages] == [
        ("user", seed_prompt, "request_seed"),
        ("assistant", seed_answer, "run_seed"),
    ]
    contract = resolve_task_continuation(
        workspace_id=workspace_id,
        session_id=session_id,
        user_input=scope_input,
        messages=messages,
    )
    assert contract is not None
    assert contract["relation"]["kind"] == "scope"
    assert contract["bootstrap"] is False
    assert contract["validation"]["expected_total_items"] == 3


def test_session_history_lifecycle_preserves_message_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from agent.app.facade import _restore_session_history
    from agent.core.session import AgentSession
    from agent.runtime.ssot_runtime import _sync_session_history
    from storage.message_store import SessionMessageStore

    workspace_id = "ws-message-identity"
    session_id = "session-message-identity"
    request_id = "request-stable-identity"
    run_id = "run-terminal-identity"
    store = SessionMessageStore(session_id=session_id, ws_id=workspace_id)
    store.write_message(
        "request_persisted",
        "user",
        "已持久化请求",
        metadata={"client_request_id": request_id},
    )
    store.write_message("run_persisted", "assistant", "已持久化答复", metadata={})

    restored = AgentSession(session_id=session_id, workspace_id=workspace_id)
    _restore_session_history(restored, session_id, workspace_id)
    assert restored.history[0].message_id == "request_persisted:user"
    assert restored.history[0].run_id == "request_persisted"
    assert restored.history[0].client_request_id == request_id
    assert restored.history[1].message_id == "run_persisted:assistant"
    fresh = AgentSession(session_id="fresh-identity", workspace_id=workspace_id)
    _sync_session_history(
        fresh,
        "本轮请求",
        "本轮答复",
        run_id=run_id,
        client_request_id=request_id,
    )
    assert fresh.history[0].message_id == f"{request_id}:user"
    assert fresh.history[0].client_request_id == request_id
    assert fresh.history[1].message_id == f"{run_id}:assistant"
    assert fresh.history[1].run_id == run_id
    assert fresh.history[1].client_request_id == request_id
