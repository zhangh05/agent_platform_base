# agent/protocol/event.py
"""AgentEvent — lifecycle events emitted during turn execution."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class AgentEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    turn_id: str = ""
    type: str = ""  # see EVENT_TYPES
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# Standard event types
SESSION_STARTED = "session_started"
TURN_STARTED = "turn_started"
CONTEXT_BUILT = "context_built"
MODEL_REQUEST_STARTED = "model_request_started"
MODEL_RESPONSE_RECEIVED = "model_response_received"
TOOL_CALL_STARTED = "tool_call_started"
TOOL_CALL_FINISHED = "tool_call_finished"
TOOL_CALL_FAILED = "tool_call_failed"
ASSISTANT_MESSAGE = "assistant_message"
TURN_FINISHED = "turn_finished"
TURN_FAILED = "turn_failed"
WARNING = "warning"
