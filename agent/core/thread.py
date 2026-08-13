# agent/core/thread.py
"""AgentThread — thin submit wrapper; delegates to session."""

from dataclasses import dataclass
from agent.runtime.result import AgentResult


@dataclass
class AgentThread:
    session: object  # AgentSession

    def submit(self, op) -> "AgentResult":
        return self.session.submit(op)
