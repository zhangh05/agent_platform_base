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
