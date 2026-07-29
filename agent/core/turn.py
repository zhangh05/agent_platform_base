# agent/core/turn.py
"""AgentTurn — one user message → one or more LLM rounds."""

import uuid
from dataclasses import dataclass, field
from agent.protocol.op import AgentOp


@dataclass
class AgentTurn:
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    op: AgentOp = None
    context: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    final_response: str = ""
    status: str = "pending"  # pending | running | finished | failed
    tool_calls: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @classmethod
    def from_op(cls, op: AgentOp) -> "AgentTurn":
        return cls(op=op)
