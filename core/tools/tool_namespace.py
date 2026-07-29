"""v3.0 canonical-only tool namespace.

Public identity contract:

  - canonical_tool_id is the ONLY public tool identifier.
  - handler_id is an internal-only implementation key. It is never
    exposed to the LLM, frontend, public catalog, or docs main tables.

Calls that pass non-canonical IDs will raise KeyError through
get_namespace_entry().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.tools.tool_namespace_data import NS_DATA, CATEGORY_DEFS


VALID_STATUSES = ("active", "disabled", "internal", "forbidden")


@dataclass(frozen=True)
class ToolNamespaceEntry:
    canonical_tool_id: str
    category: str
    group: str
    action: str
    display_name: str
    short_label: str
    usage_hint: str
    not_for: str
    handler_id: str

    def metadata(self) -> dict[str, Any]:
        """Public metadata for the catalog / API / docs.

        handler_id is intentionally NOT exposed here. It is internal-only
        and only lives on the dataclass attribute itself.
        """
        return {
            "canonical_tool_id": self.canonical_tool_id,
            "category": self.category,
            "group": self.group,
            "action": self.action,
            "display_name": self.display_name,
            "short_label": self.short_label,
            "usage_hint": self.usage_hint,
            "not_for": self.not_for,
        }


def _build_namespace() -> dict[str, ToolNamespaceEntry]:
    entries: dict[str, ToolNamespaceEntry] = {}
    for (
        canonical_id,
        category,
        group,
        action,
        display_name,
        short_label,
        usage_hint,
        not_for,
        handler_id,
    ) in NS_DATA:
        if canonical_id in entries:
            raise ValueError(
                f"duplicate canonical_tool_id in namespace data: {canonical_id}"
            )
        entries[canonical_id] = ToolNamespaceEntry(
            canonical_tool_id=canonical_id,
            category=category,
            group=group,
            action=action,
            display_name=display_name,
            short_label=short_label,
            usage_hint=usage_hint,
            not_for=not_for,
            handler_id=handler_id or canonical_id,
        )
    return entries


TOOL_NAMESPACE: dict[str, ToolNamespaceEntry] = _build_namespace()


def is_canonical(tool_id: str) -> bool:
    return tool_id in TOOL_NAMESPACE


def get_namespace_entry(tool_id: str) -> ToolNamespaceEntry:
    if tool_id not in TOOL_NAMESPACE:
        raise KeyError(f"unknown tool namespace id: {tool_id}")
    return TOOL_NAMESPACE[tool_id]


def get_canonical_tool_id(tool_id: str) -> str:
    """Return the canonical_tool_id for the given tool id.

    v3.0: there is no alias layer. If ``tool_id`` is already a
    canonical_tool_id, return it. If not, return the input as-is (used
    by router test shims that exercise the router in isolation with
    synthetic IDs).
    """
    return tool_id


def metadata_for_tool(tool_id: str) -> dict[str, Any]:
    try:
        meta = get_namespace_entry(tool_id).metadata()
    except KeyError:
        meta = {
            "canonical_tool_id": tool_id,
            "category": tool_id.split(".", 1)[0] if "." in tool_id else "runtime",
            "group": "misc",
            "action": "use",
            "display_name": tool_id,
            "short_label": tool_id,
            "usage_hint": f"Use {tool_id} when specifically needed.",
            "not_for": "Do not use outside its documented safety boundary.",
            "handler_id": tool_id,
        }
    try:
        # v3.9.3: tool_governance removed. All canonical tools are active by
        # default; an unknown id is the only case that needs a 'forbidden' tag.
        meta.update({
            "governance_status": "forbidden" if tool_id not in TOOL_NAMESPACE else "active",
            "governance_reason": "" if tool_id in TOOL_NAMESPACE else "unknown canonical_tool_id",
            "planner_visible": tool_id in TOOL_NAMESPACE,
        })
    except Exception:
        pass
    return meta


def enrich_spec(spec):
    """Attach namespace metadata to either ToolSpec dataclass variant."""
    tool_id = getattr(spec, "tool_id", "")
    base = dict(getattr(spec, "metadata", {}) or {})
    base.update(metadata_for_tool(tool_id))
    spec.metadata = base
    return spec


def category_tree_from_specs(specs: list) -> list[dict[str, Any]]:
    by_category: dict[str, dict[str, Any]] = {}
    for spec in specs:
        meta = metadata_for_tool(getattr(spec, "tool_id", ""))
        category_id = meta["category"]
        group_id = meta["group"]
        cat = by_category.setdefault(category_id, {
            "id": category_id,
            "name": CATEGORY_DEFS.get(category_id, {}).get("name", category_id),
            "description": CATEGORY_DEFS.get(category_id, {}).get("description", ""),
            "count": 0,
            "groups": {},
        })
        group = cat["groups"].setdefault(group_id, {
            "id": group_id,
            "name": group_id.replace("_", " ").title(),
            "count": 0,
            "tools": [],
        })
        tool = {
            **meta,
            "tool_id": getattr(spec, "tool_id", ""),
            "canonical_tool_id": meta["canonical_tool_id"],
            "risk_level": getattr(spec, "risk_level", "low"),
            "requires_approval": bool(getattr(spec, "requires_approval", False)),
            "permission_action": getattr(spec, "permission_action", ""),
            "enabled": bool(getattr(spec, "enabled", True)),
            "callable_by_llm": bool(getattr(spec, "callable_by_llm", True)),
            "description": getattr(spec, "description", ""),
        }
        group["tools"].append(tool)
        group["count"] += 1
        cat["count"] += 1

    categories = []
    for category_id in sorted(by_category):
        cat = by_category[category_id]
        groups = []
        for group_id in sorted(cat["groups"]):
            group = cat["groups"][group_id]
            group["tools"].sort(key=lambda t: t["canonical_tool_id"])
            groups.append(group)
        cat["groups"] = groups
        categories.append(cat)
    return categories


# ── All canonical tool IDs ──
# Every canonical tool is visible to the LLM. Keep this derived from
# TOOL_NAMESPACE so namespace/catalog/registry cannot drift by hand.
ALL_TOOL_IDS = list(TOOL_NAMESPACE.keys())
