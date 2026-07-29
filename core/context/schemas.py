# context/schemas.py
"""ContextBundle, ExecutionContext, SafeLLMContext, ContextRef schemas."""

import uuid
from dataclasses import dataclass, field
from typing import Optional

from agent.runtime.utils import now_iso

@dataclass
class ContextRef:
    ref_type: str = "none"
    ref_id: str = ""
    raw_ref: str = ""
    resolved: bool = False
    resolution_error: str = ""
    metadata: dict = field(default_factory=dict)

    def as_dict(self): return self.__dict__.copy()


@dataclass
class ContextItem:
    item_id: str = field(default_factory=lambda: f"ci_{uuid.uuid4().hex[:6]}")
    item_type: str = "request"
    source: str = "request"
    priority: int = 0
    title: str = ""
    summary: str = ""
    content: dict = field(default_factory=dict)
    sensitivity: str = "internal"
    scope: str = "request"
    token_estimate: int = 0
    citation_id: str = ""
    source_id: str = ""
    metadata: dict = field(default_factory=dict)
    redaction_applied: bool = False

    def as_dict(self): return self.__dict__.copy()


@dataclass
class ContextBudget:
    max_items: int = 30
    max_chars: int = 12000
    max_memory_hits: int = 5
    max_artifact_refs: int = 10
    max_job_events: int = 20
    max_report_sections: int = 10
    max_knowledge_chunks: int = 5
    used_items: int = 0
    used_chars: int = 0
    truncated: bool = False
    truncation_reason: str = ""
    model_context_window: int = 0   # 0 = use default max_chars; >0 = model's window in chars
    dedup_enabled: bool = True       # Enable semantic dedup

    def as_dict(self): return self.__dict__.copy()


# Model context window sizes (in chars, roughly 4 chars per token).
# Updated 2026-07 — MiniMax M3 API supports 1M, guaranteed 512K;
# DeepSeek V3/V4 support 1M; GPT-4o supports 128K.
MODEL_CONTEXT_WINDOWS = {
    "minimax-m3": 512_000,           # API 1M, guaranteed minimum 512K
    "minimax-m1": 245_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-3.5-turbo": 16_000,
    "claude-3.5-sonnet": 200_000,
    "claude-3-opus": 200_000,
    "deepseek-chat": 1_000_000,
    "deepseek-v3": 1_000_000,
    "deepseek-v4": 1_000_000,
    "qwen-max": 128_000,
    "glm-4": 128_000,
}


def resolve_budget_for_model(model: str = "") -> ContextBudget:
    """Create a ContextBudget adjusted for the active LLM model's context window.

    Strategy:
      - Use 25% of the model's context window as the context budget.
      - Floor at 8000 chars, cap at 80_000 chars.
      - If model unknown, use the default 12000 chars.
    """
    model_lower = model.lower().strip()
    window = 0
    for key, size in MODEL_CONTEXT_WINDOWS.items():
        if key in model_lower:
            window = size
            break

    if window > 0:
        # 25% of window for context, with sensible bounds
        budget_chars = max(8000, min(80_000, window // 4))
    else:
        budget_chars = 12_000  # safe default

    return ContextBudget(max_chars=budget_chars, model_context_window=window)


@dataclass
class ExecutionContext:
    workspace_id: str = ""
    run_id: str = ""
    job_id: str = ""
    trace_id: str = ""
    capability_id: str = ""
    intent: str = ""
    payload_refs: list = field(default_factory=list)
    allowed_full_artifact_ids: list = field(default_factory=list)
    source_config_artifact_id: str = ""
    selected_artifact_id: str = ""
    run_record: dict = field(default_factory=dict)
    job_record: dict = field(default_factory=dict)
    artifact_records: list = field(default_factory=list)
    report_records: list = field(default_factory=list)
    workspace_state: dict = field(default_factory=dict)
    policy: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def as_dict(self): return self.__dict__.copy()


@dataclass
class SafeLLMContext:
    workspace_id: str = ""
    intent: str = ""
    user_input: str = ""
    context_ref: Optional[ContextRef] = None
    last_result_summary: str = ""
    run_summary: dict = field(default_factory=dict)
    job_summary: dict = field(default_factory=dict)
    artifact_refs: list = field(default_factory=list)
    report_refs: list = field(default_factory=list)
    memory_hits: list = field(default_factory=list)
    knowledge_hits: list = field(default_factory=list)
    context_sources: list = field(default_factory=list)
    retrieval_diagnostics: dict = field(default_factory=dict)
    verification_summary: dict = field(default_factory=dict)
    manual_review_summary: list = field(default_factory=list)
    audit_summary: dict = field(default_factory=dict)
    trace_summary: dict = field(default_factory=dict)
    capability_summary: dict = field(default_factory=dict)
    citations: list = field(default_factory=list)
    artifact_summary: dict = field(default_factory=dict)
    job_event_summary: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    policy: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def as_dict(self):
        d = self.__dict__.copy()
        if self.context_ref and hasattr(self.context_ref, 'as_dict'):
            d["context_ref"] = self.context_ref.as_dict()
        return d


@dataclass
class ContextBundle:
    context_id: str = field(default_factory=lambda: f"ctx_{uuid.uuid4().hex[:8]}")
    request_id: str = ""
    workspace_id: str = ""
    run_id: str = ""
    job_id: str = ""
    trace_id: str = ""
    intent: str = ""
    capability_id: str = ""
    context_ref: Optional[ContextRef] = None
    user_input: str = ""
    raw_items: list = field(default_factory=list)
    selected_items: list = field(default_factory=list)
    compressed_items: list = field(default_factory=list)
    execution_context: Optional[ExecutionContext] = None
    safe_llm_context: Optional[SafeLLMContext] = None
    citations: list = field(default_factory=list)
    budget: Optional[ContextBudget] = None
    policy: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    redaction_applied: bool = False

    def as_dict(self):
        return {
            "context_id": self.context_id, "workspace_id": self.workspace_id,
            "intent": self.intent, "capability_id": self.capability_id,
            "citations": self.citations, "warnings": self.warnings,
            "errors": self.errors, "budget": self.budget.as_dict() if self.budget else {},
            "redaction_applied": self.redaction_applied,
        }
