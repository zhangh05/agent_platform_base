# core/tools/registry_helpers.py
"""Lightweight helper functions extracted from canonical_registry for readability."""

from __future__ import annotations

from typing import Any


def tool_keyword_score(tool_def: dict, query: str) -> int:
    """Score a tool definition against a keyword query."""
    q = (query or "").lower().strip()
    if not q:
        return 0
    score = 0
    for field in ("tool_id", "title", "description", "keywords"):
        val = str(tool_def.get(field, "")).lower()
        score += val.count(q)
    return score


def filter_tools_for_scenario(tools: list[dict], scenario: str) -> list[dict]:
    """Filter tools relevant to a specific scenario."""
    s = (scenario or "").lower().strip()
    if not s:
        return tools
    return [t for t in tools if s in str(t.get("scenarios", "")).lower()]


def search_tool_catalog(catalog: list[dict], query: str = "", scenario: str = "",
                        limit: int = 20) -> list[dict]:
    """Search and filter the tool catalog."""
    results = catalog[:]
    if scenario:
        results = filter_tools_for_scenario(results, scenario)
    if query:
        scored = [(tool_keyword_score(r, query), r) for r in results]
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _, r in scored if _ > 0]
    return results[:limit]


def summarize_tool(tool_def: dict) -> dict[str, Any]:
    """Return a compact summary of a tool definition."""
    return {
        "tool_id": tool_def.get("tool_id", ""),
        "title": tool_def.get("title", ""),
        "action_class": tool_def.get("action_class", "read"),
        "risk": tool_def.get("risk", "low"),
    }
