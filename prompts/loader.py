# prompts/loader.py
"""Prompt registry loader and renderer — yaml-based with file templates."""

from pathlib import Path
from prompts.schemas import PromptSpec, RenderedPrompt

ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "registry.yaml"
_cache = None


class PromptNotFoundError(RuntimeError):
    """Raised when the requested prompt task is not registered."""

    def __init__(self, task: str):
        self.task = task
        super().__init__(f"prompt task not found: {task!r}")


def _load_registry_data() -> list:
    global _cache
    if _cache is not None:
        return _cache
    if not REGISTRY_PATH.is_file():
        raise FileNotFoundError(f"prompt registry not found: {REGISTRY_PATH}")

    import yaml
    data = yaml.safe_load(REGISTRY_PATH.read_text()) or {}
    prompts = [_parse_prompt(entry) for entry in data.get("prompts", [])]
    if not prompts:
        raise RuntimeError(f"prompt registry is empty: {REGISTRY_PATH}")
    _cache = prompts
    return _cache


def _parse_prompt(entry: dict) -> PromptSpec:
    inp = entry.get("input_policy", {})
    outp = entry.get("output_policy", {})
    ctx = entry.get("context_policy", {})
    return PromptSpec(
        prompt_id=entry.get("prompt_id", ""),
        task=entry.get("task", ""),
        version=entry.get("version", "v1"),
        status=entry.get("status", "enabled"),
        template_path=entry.get("template_path", ""),
        description=entry.get("description", ""),
        input_policy={
            "allow_memory": inp.get("allow_memory", True),
            "allow_artifact_summary": inp.get("allow_artifact_summary", True),
            "allow_report_summary": inp.get("allow_report_summary", True),
            "allow_job_summary": inp.get("allow_job_summary", True),
            "allow_trace_summary": inp.get("allow_trace_summary", True),
            "allow_knowledge_chunks": inp.get("allow_knowledge_chunks", False),
            "allow_full_source": inp.get("allow_full_source", False),
            "allow_full_output": inp.get("allow_full_output", False),
            "allow_full_artifact_content": inp.get("allow_full_artifact_content", False),
            "allow_secret": inp.get("allow_secret", False),
            "max_context_chars": inp.get("max_context_chars", 8000),
        },
        output_policy={
            "forbid_fake_execution": outp.get("forbid_fake_execution", True),
            "forbid_unverified_mutation": outp.get("forbid_unverified_mutation", True),
            "forbid_hide_review": outp.get("forbid_hide_review", True),
            "forbid_unverified_execution_claim": outp.get("forbid_unverified_execution_claim", True),
            "forbid_fake_trace_or_job_status": outp.get("forbid_fake_trace_or_job_status", True),
            "forbid_fake_artifact_id": outp.get("forbid_fake_artifact_id", True),
            "forbid_secret_output": outp.get("forbid_secret_output", True),
        },
        context_policy={
            "require_safe_llm_context": ctx.get("require_safe_llm_context", True),
            "require_citations_for_references": ctx.get("require_citations_for_references", True),
            "max_artifact_refs": ctx.get("max_artifact_refs", 10),
            "max_memory_hits": ctx.get("max_memory_hits", 5),
            "max_knowledge_hits": ctx.get("max_knowledge_hits", 8),
            "max_citations": ctx.get("max_citations", 20),
        },
    )


def load_prompt_registry() -> list:
    return _load_registry_data()


def get_prompt_by_task(task: str) -> PromptSpec:
    """Return the ``PromptSpec`` registered for ``task``.

    An unknown task raises :class:`PromptNotFoundError` so
    the caller either picks a known task or surfaces the missing
    entry explicitly.

    The strict behavior is enforced at every internal call site
    — a misregistered task is a platform bug, not a runtime
    fallback case. If you genuinely need a generic catch-all,
    register a prompt with task ``"default_chat"`` (or similar)
    and call this function with that name.
    """
    if not task:
        raise PromptNotFoundError(task)
    reg = _load_registry_data()
    for p in reg:
        if p.task == task:
            return p
    raise PromptNotFoundError(task)


def get_prompt(prompt_id: str) -> PromptSpec:
    for p in _load_registry_data():
        if p.prompt_id == prompt_id:
            return p
    return None


def list_prompts() -> list:
    return [p.as_dict() for p in _load_registry_data()]


def render_prompt(task: str, safe_context: dict = None, user_input: str = "",
                  citations: list = None, extra: dict = None) -> "RenderedPrompt":
    """Render a prompt template with safe context — delegates to renderer.py."""
    from prompts.renderer import render_prompt as _render
    return _render(task, safe_context=safe_context, user_input=user_input,
                   citations=citations, extra=extra)


def validate_prompt_registry() -> dict:
    errors = []
    prompt_ids: set[str] = set()
    tasks: set[str] = set()
    project_root = ROOT.parent
    for p in _load_registry_data():
        if not p.prompt_id or p.prompt_id in prompt_ids:
            errors.append(f"duplicate or empty prompt_id: {p.prompt_id!r}")
        prompt_ids.add(p.prompt_id)
        if not p.task or p.task in tasks:
            errors.append(f"duplicate or empty task: {p.task!r}")
        tasks.add(p.task)
        template_path = Path(p.template_path)
        if not template_path.is_absolute():
            template_path = project_root / template_path
        if not template_path.is_file():
            errors.append(f"{p.prompt_id}: template not found: {p.template_path}")
        if p.output_policy.get("forbid_fake_execution") is not True:
            errors.append(f"{p.prompt_id}: fake execution not forbidden")
        if p.input_policy.get("allow_full_source") is not False:
            errors.append(f"{p.prompt_id}: allows full source")
        if p.input_policy.get("allow_secret") is not False:
            errors.append(f"{p.prompt_id}: allows secrets")
    return {"valid": len(errors) == 0, "errors": errors}
