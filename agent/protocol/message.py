# agent/protocol/message.py
"""Message types for LLM conversation protocol."""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class UserMessage:
    role: str = "user"
    content: str = ""

    def to_llm_message(self):
        from agent.llm.schemas import LLMMessage
        return LLMMessage(role="user", content=self.content)


@dataclass
class SystemMessage:
    role: str = "system"
    content: str = ""

    def to_llm_message(self):
        from agent.llm.schemas import LLMMessage
        return LLMMessage(role="system", content=self.content)


@dataclass
class AssistantMessage:
    role: str = "assistant"
    content: str = ""
    tool_calls: list = field(default_factory=list)

    def to_llm_message(self):
        from agent.llm.schemas import LLMMessage
        return LLMMessage(role="assistant", content=self.content, tool_calls=self.tool_calls)


@dataclass
class ToolResultMessage:
    role: str = "tool"
    content: str = ""
    tool_call_id: str = ""

    def to_llm_message(self):
        from agent.llm.schemas import LLMMessage
        return LLMMessage(role="tool", content=self.content, tool_call_id=self.tool_call_id)


@dataclass
class RuntimeContextMessage:
    """RuntimeSnapshot injected as system-level context."""
    role: str = "system"
    content: str = ""

    def to_llm_message(self):
        from agent.llm.schemas import LLMMessage
        return LLMMessage(role="system", content=self.content)
