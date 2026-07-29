"""Cached, canonical projection of the public tool catalog."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

CATALOG_VERSION = "tool_catalog.v2"


def _permission_action(action_class: str) -> str:
    mapping = {
        "read": "read",
        "write": "write",
        "execute": "exec",
        "network": "network",
        "delete": "write",
        "admin": "write",
    }
    return mapping.get(str(action_class or "read"), "read")


_WRITE_ACTIONS = {
    "add", "create", "update", "confirm", "review", "profile_set",
    "save", "tag", "edit", "patch", "write_artifact", "import",
    "session_checkpoint", "review_update", "baseline_create",
    "check", "topology_build", "impact", "incident_create",
    "incident_update", "change_create", "change_precheck",
    "change_postcheck", "change_update", "schedule_create",
    "schedule_update", "schedule_run",
}
_READ_ACTIONS = {
    "list", "get", "status", "search", "read", "chunk", "profile_get",
    "load", "inspect", "diagnostics", "health", "selfcheck", "local_info",
    "tasks", "audit_log", "run_get", "session_get", "session_snapshot",
    "review_list", "overview", "baseline_list", "check_get", "check_list",
    "drift_list", "topology_get", "operation_get", "operation_list",
    "incident_list", "change_list", "schedule_list", "report", "summary",
    "filter", "protocol", "align", "scan", "parse", "stats", "distinct",
    "aggregate", "sort", "render", "pivot", "join", "extract", "match",
    "redact", "diff", "document", "references", "read_image", "glob",
}
_EXEC_ACTIONS = {"shell", "python", "slash", "background", "stream"}
_NETWORK_ACTIONS = {"search", "fetch", "weather", "deep_search"}


def _action_permission(tool_id: str, action: str, base_permission: str) -> str:
    action = str(action or "").strip().lower()
    if tool_id == "exec.run" or action in _EXEC_ACTIONS:
        return "exec"
    if tool_id == "web.manage" or action in _NETWORK_ACTIONS:
        return "network"
    if action in _WRITE_ACTIONS or action in {"delete", "remove", "purge", "destroy", "drop", "rewind", "session_rewind"}:
        return "write"
    if action in _READ_ACTIONS:
        return "read"
    return base_permission or "read"


def _action_profiles(tool_id: str, actions: list[str], *, input_schema: dict, category: str, base_permission: str) -> list[dict]:
    if not actions:
        return []
    try:
        from core.tools.policy import ToolPolicy
        from core.tools.schemas import ToolInvocation, ToolSpec
        from core.tools.manifest_registry import get_manifest
        manifest = get_manifest(tool_id)
        policy = ToolPolicy()
    except Exception:
        policy = None
        manifest = None
        ToolInvocation = ToolSpec = None

    profiles = []
    for action in actions:
        risk_level = getattr(manifest, "risk_level", "low") if manifest else "low"
        requires_approval = bool(getattr(manifest, "requires_approval", False)) if manifest else False
        if policy and ToolInvocation and ToolSpec:
            decision = policy.check(
                ToolSpec(
                    tool_id=tool_id,
                    name=tool_id,
                    description=tool_id,
                    category=category or "tool",
                    risk_level=risk_level,
                    requires_approval=requires_approval,
                    input_schema=input_schema or {},
                ),
                ToolInvocation(
                    tool_id=tool_id,
                    arguments={"action": action},
                    workspace_id="default",
                    requested_by="catalog",
                ),
            )
            risk_level = decision.risk_level or risk_level
            requires_approval = bool(decision.requires_approval)
        profiles.append({
            "action": action,
            "risk_level": risk_level,
            "requires_approval": requires_approval,
            "permission_action": _action_permission(tool_id, action, base_permission),
        })
    return profiles


@lru_cache(maxsize=1)
def build_catalog_snapshot() -> dict:
    # v3.9.3: capability_actions and tool_governance modules removed.
    # Each canonical_id is its own "capability action" (1:1).
    from core.tools.canonical_registry import CANONICAL_REGISTRY, list_canonical_ids
    from core.tools.tool_namespace import TOOL_NAMESPACE, category_tree_from_specs, metadata_for_tool

    tools = []
    # v3.10 Phase 5: enrich with Capability Manifest fields
    try:
        from core.tools.manifest_registry import get_manifest as _gm
    except Exception:
        _gm = None

    for canonical_id in list_canonical_ids():
        cr_entry = CANONICAL_REGISTRY[canonical_id]
        meta = metadata_for_tool(canonical_id)
        manifest = _gm(canonical_id) if _gm else None

        action_class = manifest.action_class if manifest else "read"
        permission_action = cr_entry.permission_action or _permission_action(action_class)

        actions = _schema_actions(cr_entry.input_schema)

        item = {
            "tool_id": canonical_id,
            "canonical_tool_id": canonical_id,
            "display_name": meta["display_name"],
            "category": meta["category"],
            "group": meta["group"],
            "action": meta["action"],
            "usage_hint": meta.get("usage_hint", ""),
            "not_for": meta.get("not_for", ""),
            "description": manifest.description if manifest else cr_entry.description,
            "risk_level": manifest.risk_level if manifest else cr_entry.risk_level,
            "requires_approval": manifest.requires_approval if manifest else bool(cr_entry.requires_approval),
            "input_schema": cr_entry.input_schema,
            "permission_action": permission_action,
            "callable_by_llm": True,
            "enabled": True,
            "governance_status": meta["governance_status"],
            "planner_visible": bool(meta["planner_visible"]),
            "capability_actions": [canonical_id],  # v3.9.3: 1:1 with canonical
            # v3.10 Phase 5: Capability Manifest fields
            "destructive": manifest.destructive if manifest else False,
            "idempotency": manifest.idempotency if manifest else "unknown",
            "side_effects": manifest.side_effects if manifest else "none",
            "output_sensitivity": manifest.output_sensitivity if manifest else "internal",
            "timeout_seconds": manifest.timeout_seconds if manifest else 30,
            "action_class": action_class,
            "approval_reason": manifest.approval_reason_template if manifest else "",
            "rollback_strategy": manifest.rollback_strategy if manifest else "none",
            "allowed_callers": manifest.allowed_callers if manifest else ["turn_runner"],
            "reads_artifact": manifest.reads_artifact if manifest else False,
            "writes_artifact": manifest.writes_artifact if manifest else False,
            "secret_fields": manifest.secret_fields if manifest else [],
            "actions": actions,
            "action_profiles": _action_profiles(
                canonical_id,
                actions,
                input_schema=cr_entry.input_schema,
                category=meta["category"],
                base_permission=permission_action,
            ),
        }
        tools.append(item)
    tools.sort(key=lambda item: item["canonical_tool_id"])

    class _Spec:
        def __init__(self, item):
            canonical_id = item["canonical_tool_id"]
            registry_entry = CANONICAL_REGISTRY[canonical_id]
            self.tool_id = canonical_id
            self.metadata = {
                "canonical_tool_id": canonical_id,
                "category": item["category"],
                "group": item["group"],
                "action": item["action"],
                "display_name": item["display_name"],
                "short_label": canonical_id,
                "usage_hint": "",
                "not_for": "",
                "handler_id": registry_entry.handler_id,
                "governance_status": item["governance_status"],
                "governance_reason": "",
                "planner_visible": item["planner_visible"],
            }
            self.risk_level = item["risk_level"]
            self.requires_approval = item["requires_approval"]
            self.permission_action = item["permission_action"]
            self.enabled = item["enabled"]
            self.callable_by_llm = item["callable_by_llm"]
            self.description = item["description"]

    categories = category_tree_from_specs([_Spec(item) for item in tools])
    fingerprint_input = [
        {
            "id": item["canonical_tool_id"],
            "governance": item["governance_status"],
            "visible": item["planner_visible"],
            "risk": item["risk_level"],
        }
        for item in tools
    ]
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_input, sort_keys=True).encode("utf-8"),
    ).hexdigest()[:16]
    return {
        "tools": tools,
        "categories": categories,
        "count": len(tools),
        "planner_visible_count": len(list(TOOL_NAMESPACE)),
        "governance_summary": {'active': 0, 'disabled': 0, 'internal': 0, 'forbidden': 0},
        "catalog_version": CATALOG_VERSION,
        "catalog_fingerprint": fingerprint,
        "cache_policy": "process_static",
        "note": (
            "Read-only catalog. canonical_tool_id is the only public "
            "tool ID; handler_id is internal-only."
        ),
    }


def reset_catalog_snapshot_cache() -> None:
    build_catalog_snapshot.cache_clear()


def _schema_actions(schema: dict) -> list[str]:
    """Return declared action enum values from a canonical tool schema."""
    try:
        action = (schema or {}).get("properties", {}).get("action", {})
        values = action.get("enum", [])
        if isinstance(values, list):
            return [str(v) for v in values]
    except Exception:
        return []
    return []
