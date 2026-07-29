# prompts/schemas.py
"""PromptSpec, RenderedPrompt schemas."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PromptSpec:
    prompt_id: str = ""
    task: str = ""
    version: str = "v1"
    status: str = "enabled"
    template_path: str = ""
    description: str = ""
    allowed_providers: list = field(default_factory=lambda: ["minimax", "openai", "deepseek", "ollama", "mock"])
    input_policy: dict = field(default_factory=lambda: {
        "allow_memory": True, "allow_artifact_summary": True,
        "allow_report_summary": True, "allow_job_summary": True,
        "allow_trace_summary": True, "allow_knowledge_chunks": False,
        "allow_full_source": False, "allow_full_output": False,
        "allow_full_artifact_content": False, "allow_secret": False,
        "max_context_chars": 8000,
    })
    output_policy: dict = field(default_factory=lambda: {
        "forbid_fake_execution": True, "forbid_unverified_mutation": True,
        "forbid_hide_review": True, "forbid_unverified_execution_claim": True,
        "forbid_fake_trace_or_job_status": True, "forbid_fake_artifact_id": True,
        "forbid_secret_output": True,
    })
    context_policy: dict = field(default_factory=lambda: {
        "require_safe_llm_context": True, "require_citations_for_references": True,
        "max_artifact_refs": 10, "max_memory_hits": 5,
        "max_knowledge_hits": 8, "max_citations": 20,
    })
    tests: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def as_dict(self): return self.__dict__.copy()


@dataclass
class RenderedPrompt:
    prompt_id: str = ""
    task: str = ""
    version: str = ""
    text: str = ""
    context_chars: int = 0
    citation_ids: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def as_dict(self): return self.__dict__.copy()
