# agent/llm/context_builder.py
"""Safe context builder — strips secrets, limits data to summary only."""

from agent.state import AgentState
from agent.llm.policy import SECRET_PATTERNS

MAX_SAMPLES = 5
MAX_MEMORY_HITS = 3
MAX_MEMORY_PREVIEW_CHARS = 120


def build_safe_context(state: AgentState) -> dict:
    """Build a safe context dict for LLM consumption. No raw configs, no secrets."""

    result = state.tool_results or state.skill_results or {}
    verification = state.verification or {}

    ctx = {
        "intent": state.intent,
        "verification_status": verification.get("status", "unknown"),
    }

    # Memory hits summary — compact preview only. Detailed recall should go
    # through memory.manage(search) so prompt input stays bounded.
    ctx["memory_hits_summary"] = [
        {
            "title": h.get("title", ""),
            "content_preview": (h.get("content", "") or "")[:MAX_MEMORY_PREVIEW_CHARS],
            "type": h.get("memory_type", ""),
        }
        for h in (state.memory_hits or [])[:MAX_MEMORY_HITS]
    ]

    # Module status
    ctx["module_status"] = state.context.get("modules", {})
    ctx["planned_modules"] = [
        m for m, s in state.context.get("modules", {}).items() if s == "planned"
    ]

    # Artifact summary (max 10, no content/path/secret/temp)
    artifact_refs = state.context.get("artifact_refs", [])
    safe_refs = [
        {
            "artifact_id": r.get("artifact_id", ""),
            "artifact_type": r.get("artifact_type", ""),
            "title": r.get("title", ""),
            "summary": r.get("summary", ""),
            "scope": r.get("scope", ""),
            "sensitivity": r.get("sensitivity", ""),
            "metadata": r.get("metadata", {}),
        }
        for r in artifact_refs[:10]
        if r.get("sensitivity") != "secret" and r.get("scope") != "temp"
    ]
    if safe_refs:
        ctx["artifact_refs"] = safe_refs
        ctx["artifact_summary"] = {
            "input_count": sum(1 for r in safe_refs if str(r.get("artifact_type", "")).endswith("input")),
            "output_count": sum(1 for r in safe_refs if str(r.get("artifact_type", "")).endswith("output")),
            "sensitive_count": sum(1 for r in safe_refs if r.get("sensitivity") == "sensitive"),
            "total": len(safe_refs),
        }

    return ctx


def _redact_samples(items: list) -> list:
    """Redact secrets from sample items."""
    cleaned = []
    for item in items:
        d = {}
        for k, v in item.items():
            if any(secret in str(k).lower() for secret in SECRET_PATTERNS):
                d[k] = "[REDACTED]"
            elif any(secret in str(v).lower() for secret in SECRET_PATTERNS):
                d[k] = "[REDACTED]"
            else:
                d[k] = v
        cleaned.append(d)
    return cleaned
