# prompts/renderer.py
"""Strict renderer for the small template language used by prompt files."""

import json
import re
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class RenderedPrompt:
    prompt_id: str = ""
    task: str = ""
    version: str = "v1"
    text: str = ""
    context_chars: int = 0
    citation_ids: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def as_dict(self): return self.__dict__.copy()


def render_prompt(task: str, safe_context: dict = None, user_input: str = "",
                  citations: list = None, extra: dict = None) -> RenderedPrompt:
    """Render a registered prompt with complete, explicitly referenced context."""
    from prompts.loader import get_prompt_by_task

    spec = get_prompt_by_task(task)
    citations = list(citations or [])
    merged_context = dict(safe_context or {})
    merged_context.update(dict(extra or {}))
    ctx, policy_warnings = _apply_context_policy(
        merged_context, citations, spec
    )
    vars_ctx = dict(ctx)
    vars_ctx["user_input"] = user_input
    vars_ctx["citations"] = citations

    # Load template file
    template_text = ""
    if spec.template_path:
        tp = Path(spec.template_path)
        ROOT = Path(__file__).resolve().parent.parent
        tpath = ROOT / tp if not tp.is_absolute() else tp
        if tpath.is_file():
            template_text = tpath.read_text(encoding="utf-8")

    if not template_text:
        raise FileNotFoundError(
            f"prompt template not found for {spec.prompt_id}: {spec.template_path}"
        )

    vars_ctx["task"] = task
    text = _render_template(template_text, vars_ctx)
    unresolved = sorted(set(re.findall(r"(?:\{\{|\{%)[^\n]{0,120}", text)))
    if unresolved:
        raise ValueError(
            f"unresolved template expressions in {spec.prompt_id}: {unresolved[:3]}"
        )

    return RenderedPrompt(
        prompt_id=spec.prompt_id, task=task, version=spec.version,
        text=text, context_chars=len(_safe_json(ctx)),
        citation_ids=[c.get("citation_id", "") for c in citations],
        warnings=policy_warnings,
        metadata={
            "context_policy_applied": True,
            "max_context_chars": int(spec.input_policy.get("max_context_chars", 8000)),
        },
    )


def _render_template(text: str, values: dict) -> str:
    """Render conditionals, loops, variables and allowlisted filters."""
    text = _replace_conditionals(text, values)
    text = _replace_loops(text, values)
    return _replace_variables(text, values)


def _replace_conditionals(text: str, values: dict) -> str:
    """Resolve simple truthy ``if`` blocks without evaluating expressions."""

    pattern = re.compile(r'\{%\s*if\s+([a-zA-Z_][\w.]*)\s*%\}([\s\S]*?)\{%\s*endif\s*%\}')
    previous = None
    while previous != text:
        previous = text

        def repl(match):
            val = _resolve_path(values, match.group(1))
            branches = re.split(r'\{%\s*else\s*%\}', match.group(2), maxsplit=1)
            if val:
                return branches[0]
            return branches[1] if len(branches) == 2 else ""

        text = pattern.sub(repl, text)
    return text


def _replace_loops(text: str, values: dict) -> str:
    """Resolve loops over bounded lists from the trusted render context."""
    pattern = re.compile(
        r'\{%\s*for\s+([a-zA-Z_]\w*)\s+in\s+([a-zA-Z_][\w.]*)\s*%\}'
        r'([\s\S]*?)\{%\s*endfor\s*%\}'
    )
    previous = None
    while previous != text:
        previous = text

        def repl(match):
            item_name, path, body = match.groups()
            items = _resolve_path(values, path)
            if not isinstance(items, (list, tuple)):
                return ""
            rendered = []
            for item in items:
                item_values = dict(values)
                item_values[item_name] = item
                rendered.append(_replace_variables(body, item_values))
            return "".join(rendered)

        text = pattern.sub(repl, text)
    return text


def _replace_variables(text: str, values: dict) -> str:
    """Resolve scalar variables with a deliberately small filter allow-list."""
    pattern = re.compile(
        r'\{\{\s*([a-zA-Z_][\w.]*)'
        r'(?:\s*\|\s*([a-zA-Z_]\w*))?\s*\}\}'
    )

    def repl(match):
        var_name = match.group(1)
        filter_name = match.group(2) or ""
        val = _resolve_path(values, var_name)
        if filter_name == "summary_only":
            return _summary_only(val)
        if filter_name == "upper":
            return _stringify(val).upper()
        if filter_name:
            raise ValueError(f"unsupported prompt filter: {filter_name}")
        return _stringify(val)

    return pattern.sub(repl, text)


def _resolve_path(values: dict, path: str):
    cur = values
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def _summary_only(value) -> str:
    if not value:
        return ""
    if isinstance(value, dict):
        safe = {
            str(k): v for k, v in value.items()
            if str(k).lower() not in {"secret", "password", "token", "api_key", "key", "credential"}
        }
        for key in ("summary", "status", "title", "message"):
            if safe.get(key):
                return str(safe.get(key))
        return _safe_json(safe)
    return str(value)


def _apply_context_policy(ctx: dict, citations: list, spec) -> tuple[dict, list[str]]:
    """Preserve every rendered context item and citation.

    Registry budgets remain provider-capacity telemetry only; they do not
    authorize deleting context before the model can reason over it.
    """
    return dict(ctx), []


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value).replace("\x00", "")


def _safe_json(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)
