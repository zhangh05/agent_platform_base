"""Skill tool handlers — business capability catalog projection.

v3.9.4: skill.manage reads agent.capabilities.catalog. It exposes guidance
and recommended canonical tools only; it does not register tools, change
visibility, or bypass manifest policy.
"""

from __future__ import annotations

from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id

from core.tools.general_tools.shared import _caller_workspace, _contract, _error, _error_inv, _ok, _result, _unavailable, _workspace_path


# v3.9.4 projected from agent.capabilities.catalog

def _pkg_as_dict(pkg: dict) -> dict:
    """Skill dict (v3.9.4: delegates to business capability catalog)."""
    from agent.capabilities.catalog import to_skill_dict
    d = to_skill_dict(pkg)
    # The frontend displays enabled packages as active.
    d["status"] = "active"
    return d


def _search_packages(query: str, limit: int = 10) -> list[dict]:
    """Keyword search in the business capability catalog."""
    q = (query or "").lower().strip()
    if not q:
        return []
    from agent.capabilities import catalog as _catalog
    matches = []
    for pkg in _catalog.list_all():
        haystack = " ".join([
            pkg["capability_id"], pkg["display_name"], pkg["description"],
            " ".join(pkg["module_ids"]), " ".join(pkg["recommended_tool_ids"]),
        ]).lower()
        if q in haystack:
            matches.append(_pkg_as_dict(pkg))
    return matches[:max(1, min(limit, 20))]


# ── tool handlers ──

def handle_skill_list(inv: ToolInvocation) -> dict:
    """List all skill packages."""
    try:
        from agent.capabilities import catalog as _catalog
        results = [_pkg_as_dict(c) for c in _catalog.list_all()]
        return _ok(inv, "", {"results": results, "count": len(results)})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_skill_load(inv: ToolInvocation) -> dict:
    """Load a skill by capability_id; returns tool_ids, prompt_hints, etc."""
    args = inv.arguments or {}
    skill_name = str(args.get("skill_name", "")).strip()
    if not skill_name:
        return _error_inv(inv, "skill_name is required")

    from agent.capabilities import catalog as _catalog
    # Direct lookup
    for pkg in _catalog.list_all():
        if pkg["capability_id"] == skill_name:
            payload = {
                "skill_id": pkg["capability_id"],
                "status": "active",
                "capability_ids": [pkg["capability_id"]],
                "module_ids": list(pkg["module_ids"]),
                "tool_ids": list(pkg["recommended_tool_ids"]),
                "prompt_hints": list(pkg["prompt_hints"]),
                "safety_notes": list(pkg["safety_notes"]),
                "message": "skill loaded",
            }
            payload["skill_record"] = {k: v for k, v in payload.items() if k != "message"}
            return _ok(inv, "", payload)

    # Fuzzy match
    lower = skill_name.lower()
    for pkg in _catalog.list_all():
        if lower in pkg["capability_id"].lower() or lower in pkg["display_name"].lower():
            payload = {
                "skill_id": pkg["capability_id"],
                "status": "active",
                "capability_ids": [pkg["capability_id"]],
                "module_ids": list(pkg["module_ids"]),
                "tool_ids": list(pkg["recommended_tool_ids"]),
                "prompt_hints": list(pkg["prompt_hints"]),
                "safety_notes": list(pkg["safety_notes"]),
                "message": "skill loaded (fuzzy match)",
            }
            payload["skill_record"] = {k: v for k, v in payload.items() if k != "message"}
            return _ok(inv, "", payload)

    return _error_inv(inv, f"skill '{skill_name}' not found. Available: {[c['capability_id'] for c in _catalog.list_all()]}")


def handle_skill_find(inv: ToolInvocation) -> dict:
    """Search skills by keyword."""
    args = inv.arguments or {}
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit", 10))
    if not query:
        return _error_inv(inv, "query is required")
    try:
        results = _search_packages(query, limit=limit)
        return _ok(inv, "", {"results": results, "count": len(results), "query": query})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_skill_inspect(inv: ToolInvocation) -> dict:
    """Return skill details."""
    args = inv.arguments or {}
    skill_name = str(args.get("skill_name", "")).strip()
    if not skill_name:
        return _error_inv(inv, "skill_name is required")

    from agent.capabilities import catalog as _catalog
    for pkg in _catalog.list_all():
        if pkg["capability_id"] == skill_name:
            return _ok(inv, "", _pkg_as_dict(pkg))

    return _error_inv(inv, f"skill '{skill_name}' not found")


__all__ = [
    "handle_skill_list",
    "handle_skill_load",
    "handle_skill_find",
    "handle_skill_inspect",
]
