# agent/llm/schemas.py
"""LLM schemas — task types, messages, safe output, function calling support."""

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Dict, Any, Union
from enum import Enum


class LLMTask(str, Enum):
    ASSISTANT_CHAT = "assistant_chat"
    RESPONSE_COMPOSE = "response_compose"
    MANUAL_REVIEW_EXPLAIN = "manual_review_explain"
    RESULT_SUMMARIZE = "result_summarize"
    CONTEXT_QA = "context_qa"
    JOB_FAILURE_EXPLAIN = "job_failure_explain"
    REPORT_SUMMARY = "report_summary"
    ARTIFACT_SUMMARY_EXPLAIN = "artifact_summary_explain"
    KNOWLEDGE_ANSWER = "knowledge_answer"
    MEMORY_CONSOLIDATION = "memory_consolidation"


ALLOWED_TASKS = {t.value for t in LLMTask}

BLOCKED_TASKS = {
    "call_module_directly",
    "fake_planned_module_result",
}


@dataclass
class LLMMessage:
    role: str  # system, user, assistant, tool
    content: Union[str, List[dict]]  # str for text, List[dict] for multimodal (text + image_url)
    tool_call_id: Optional[str] = None  # for role=tool responses
    tool_calls: Optional[List[dict]] = None  # for role=assistant with function_call


@dataclass
class LLMRequest:
    task: str
    messages: List[LLMMessage] = field(default_factory=list)
    safe_context: Dict[str, Any] = field(default_factory=dict)
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Server-owned execution control. This is intentionally separate from
    # request metadata so callbacks cannot enter prompts or caller input.
    cancel_check: Optional[Callable[[], bool]] = None
    tools: Optional[List[dict]] = None  # OpenAI function definitions
    stream: bool = False  # Enable token-level streaming


@dataclass
class LLMToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    # Optional dynamic-orchestration metadata.  Ordinary one-off function
    # calls leave these fields empty and keep their current behaviour.
    step_id: str = ""
    depends_on: List[str] = field(default_factory=list)
    result_bindings: Dict[str, str] = field(default_factory=dict)
    failure_policy: str = "replan"


@dataclass
class LLMResponse:
    content: str = ""
    provider: str = ""
    model: str = ""
    usage: Optional[Dict[str, Any]] = None
    finish_reason: str = ""
    raw: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    tool_calls: List[LLMToolCall] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


@dataclass
class PolicyDecision:
    allowed: bool = True
    reason: str = ""
    violations: List[str] = field(default_factory=list)
    redactions: List[str] = field(default_factory=list)


@dataclass
class SafeLLMOutput:
    summary: str = ""
    answer: str = ""
    warnings: List[str] = field(default_factory=list)
    next_questions: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    safe_to_show: bool = True
    policy_decision: Optional[PolicyDecision] = None
    llm_used: bool = False
    fallback_reason: str = ""
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "summary": self.summary,
            "answer": self.answer,
            "warnings": self.warnings,
            "safe_to_show": self.safe_to_show,
            "llm_used": self.llm_used,
            "fallback_reason": self.fallback_reason,
        }
