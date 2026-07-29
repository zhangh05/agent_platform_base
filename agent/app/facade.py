# agent/app/facade.py
"""AgentApp — main entry point for the Codex-style runtime."""

import logging
from agent.app.session_manager import SessionManager
from agent.core.thread import AgentThread
from agent.protocol.op import AgentOp

logger = logging.getLogger(__name__)


def _restore_session_history(session, session_id: str, workspace_id: str):
    """Restore session.history from disk (SessionMessageStore).

    Bridges the gap between in-memory session eviction (process restart,
    TTL expiry) and disk-persisted messages. Without this, a recreated
    session starts with an empty history, causing the "conversation
    history not showing" bug.
    """
    try:
        from storage.message_store import SessionMessageStore
        from agent.protocol.message import UserMessage, AssistantMessage
        store = SessionMessageStore(session_id=session_id, ws_id=workspace_id)  # workspace_id validated by caller
        msgs = store.get_messages()
        if msgs:
            history = []
            for m in msgs:
                role = m.get("role", "")
                content = m.get("content", "")
                if role == "user" and content:
                    history.append(UserMessage(content=content))
                elif role == "assistant" and content:
                    history.append(AssistantMessage(content=content))
            if history:
                session.history = history
                logger.info("Restored %d history messages for session %s from disk", len(history), session_id)
    except Exception as e:
        logger.warning("Failed to restore session history from disk for %s: %s", session_id, e)


class AgentApp:
    def __init__(self):
        self.session_manager = SessionManager(
            restore_callback=_restore_session_history,
        )

    def submit_user_message(
        self,
        user_input: str,
        workspace_id: str,
        session_id: str = None,
        metadata: dict = None,
    ) -> "AgentResult":
        """Submit a user message and return AgentResult.

        Session lifecycle is handled by SessionManager:
        - session map access is serialized to avoid concurrent creation races;
        - implicit sessions use UUIDs instead of sequential counters;
        - turns for the same session are serialized to prevent history,
          context, tool-result, and trace cross-talk.
        """
        sid, session, turn_lock = self.session_manager.get_or_create(session_id, workspace_id)

        # The expensive turn execution happens outside the global session-map
        # lock, but inside this session's turn lock. Different sessions can run
        # concurrently; the same session cannot interleave turns.
        with turn_lock:
            if metadata is None:
                metadata = {}
            elif not isinstance(metadata, dict):
                metadata = {}
            metadata = dict(metadata)
            metadata.setdefault("turn_serialization", "per_session")

            op = AgentOp.user_message(user_input=user_input, session_id=sid, workspace_id=workspace_id, metadata=metadata)
            thread = AgentThread(session=session)
            return thread.submit(op)

    def inspect_sessions(self) -> dict:
        """Return a snapshot of active sessions for diagnostics."""
        return self.session_manager.snapshot()
