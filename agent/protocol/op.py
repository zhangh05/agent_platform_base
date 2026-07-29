# agent/protocol/op.py
"""AgentOp — submitted by user or system to AgentApp."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class AgentOp:
    op_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "user_message"  # user_message | cancel_turn | system_event
    session_id: Optional[str] = None
    workspace_id: str = ""
    user_input: str = ""
    payload: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def user_message(cls, user_input: str, session_id: str = None, workspace_id: str = "", metadata: dict = None) -> "AgentOp":
        # session_id is intentionally NOT auto-generated. Previously we
        # minted a UUID here when no session_id was supplied, which
        # produced orphan sessions that were never persisted or
        # rejoinable. Callers must now pass an explicit session_id;
        # for new sessions the AgentApp layer is responsible for
        # creating one (via SessionStore) and routing the op to it.
        if not session_id:
            raise ValueError(
                "session_id is required — AgentApp must create a session "
                "before submitting a user_message op (no implicit UUID minting)"
            )
        return cls(
            type="user_message",
            session_id=session_id,
            workspace_id=workspace_id,
            user_input=user_input,
            metadata=metadata or {},
        )
