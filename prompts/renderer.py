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
    """Render a registered prompt with bounded, explicitly referenced context."""
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
    """Render conditionals, loops, variables and approved filters."""
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
                return str(safe.get(key))[:500]
        return _safe_json(safe)[:500]
    return str(value)[:500]


def _apply_context_policy(ctx: dict, citations: list, spec) -> tuple[dict, list[str]]:
    """Apply registry list limits and context character budget before rendering."""
    warnings: list[str] = []
    list_limits = {
        "artifact_refs": int(spec.context_policy.get("max_artifact_refs", 10)),
        "memory_hits": int(spec.context_policy.get("max_memory_hits", 5)),
        "knowledge_hits": int(spec.context_policy.get("max_knowledge_hits", 8)),
    }
    bounded = dict(ctx)
    for key, limit in list_limits.items():
        value = bounded.get(key)
        if isinstance(value, list) and len(value) > max(0, limit):
            bounded[key] = value[:max(0, limit)]
            warnings.append(f"{key}_truncated:{len(value)}->{max(0, limit)}")

    max_citations = int(spec.context_policy.get("max_citations", 20))
    if len(citations) > max_citations:
        original_count = len(citations)
        del citations[max_citations:]
        warnings.append(f"citations_truncated:{original_count}->{max_citations}")

    max_chars = max(500, int(spec.input_policy.get("max_context_chars", 8000)))
    bounded = _fit_context_budget(bounded, max_chars)
    if len(_safe_json(ctx)) > max_chars:
        warnings.append(f"context_truncated_to:{max_chars}")
    return bounded, warnings


def _fit_context_budget(value: dict, max_chars: int) -> dict:
    """Deterministically shrink string leaves while preserving context shape."""
    if len(_safe_json(value)) <= max_chars:
        return value
    cap = 1000
    bounded = value
    while cap >= 80:
        bounded = _bound_strings(value, cap)
        if len(_safe_json(bounded)) <= max_chars:
            return bounded
        cap //= 2
    bounded = _bound_strings(value, 80)
    while len(_safe_json(bounded)) > max_chars and _drop_last_list_item(bounded):
        pass
    return bounded


def _bound_strings(value, cap: int):
    if isinstance(value, dict):
        return {str(k): _bound_strings(v, cap) for k, v in value.items()}
    if isinstance(value, list):
        return [_bound_strings(v, cap) for v in value]
    if isinstance(value, tuple):
        return tuple(_bound_strings(v, cap) for v in value)
    if isinstance(value, str):
        return value if len(value) <= cap else value[:cap] + "..."
    return value


def _drop_last_list_item(value) -> bool:
    """Drop one item from the deepest non-empty list to honor a hard budget."""
    if isinstance(value, dict):
        for child in reversed(list(value.values())):
            if _drop_last_list_item(child):
                return True
        return False
    if isinstance(value, list):
        for child in reversed(value):
            if _drop_last_list_item(child):
                return True
        if value:
            value.pop()
            return True
    return False


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
