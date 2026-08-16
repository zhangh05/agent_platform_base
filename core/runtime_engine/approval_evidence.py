"""Bounded, untrusted prior-tool evidence for an approved continuation."""
from __future__ import annotations

from html import escape
import json
from typing import Any, Iterable, Mapping

from storage.redaction import redact_text, redact_value
from .context_budget import project_json_to_tokens

MAX_RESUME_EVIDENCE_ITEMS = 4
MAX_RESUME_EVIDENCE_ITEM_TOKENS = 384
MAX_RESUME_EVIDENCE_TOTAL_TOKENS = 1_600


def project_approval_resume_evidence(results: Iterable[Any]) -> list[dict[str, Any]]:
    """Create a redacted, bounded snapshot from already executed tool results.

    This projection is made inside QueryLoop immediately before a continuation
    is persisted.  It is not caller metadata and it is deliberately rendered as
    untrusted data on resume; no tool output can become a trusted instruction.
    """
    projected: list[dict[str, Any]] = []
    for result in list(results or [])[-MAX_RESUME_EVIDENCE_ITEMS:]:
        output = getattr(result, "output", {})
        safe_output = redact_value(output if isinstance(output, Mapping) else {"data": output})
        bounded_output, truncated = project_json_to_tokens(
            safe_output,
            MAX_RESUME_EVIDENCE_ITEM_TOKENS,
        )
        summary = redact_text(str(getattr(result, "summary", "") or ""))[:220]
        projected.append({
            "source_tool": redact_text(str(getattr(result, "tool_name", "") or "tool"))[:120],
            "source_call_id": redact_text(str(getattr(result, "call_id", "") or ""))[:128],
            "ok": bool(getattr(result, "ok", False)),
            "summary": summary,
            "output": bounded_output,
            "truncated": bool(truncated),
        })
    return projected


def normalize_approval_resume_evidence(value: Any) -> list[dict[str, Any]]:
    """Defensively bound a server-secret continuation payload before rendering."""
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value[:MAX_RESUME_EVIDENCE_ITEMS]:
        if not isinstance(item, Mapping):
            continue
        safe_output = redact_value(item.get("output") if isinstance(item.get("output"), Mapping) else {})
        bounded_output, truncated = project_json_to_tokens(
            safe_output,
            MAX_RESUME_EVIDENCE_ITEM_TOKENS,
        )
        normalized.append({
            "source_tool": redact_text(str(item.get("source_tool") or "tool"))[:120],
            "source_call_id": redact_text(str(item.get("source_call_id") or ""))[:128],
            "ok": bool(item.get("ok", False)),
            "summary": redact_text(str(item.get("summary") or ""))[:220],
            "output": bounded_output,
            "truncated": bool(item.get("truncated")) or bool(truncated),
        })
    return normalized


def render_approval_resume_evidence(value: Any) -> str:
    """Render the evidence as data, never as a tool protocol or trusted prompt."""
    evidence = normalize_approval_resume_evidence(value)
    if not evidence:
        return ""
    payload, _ = project_json_to_tokens(evidence, MAX_RESUME_EVIDENCE_TOTAL_TOKENS)
    return (
        '<approval_resume_evidence data_only="true" trust="untrusted_data" '
        'source_kind="prior_tool_observation">\n'
        + escape(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str), quote=False)
        + "\n</approval_resume_evidence>"
    )
