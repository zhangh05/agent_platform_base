"""Business capability API backed directly by the canonical catalog."""

from flask import jsonify

from agent.capabilities import catalog
from core.tools.manifest_registry import get_manifest


def handle_capabilities():
    def project(cap: dict) -> dict:
        risk = "low"
        for tool_id in cap["recommended_tool_ids"]:
            manifest = get_manifest(tool_id)
            if manifest.risk_level == "high":
                risk = "high"
                break
            if manifest.risk_level == "medium":
                risk = "medium"
        return {
            "capability_id": cap["capability_id"],
            "description": cap["description"],
            "category": _category(cap["capability_id"]),
            "intent": cap["capability_id"].replace("_", "."),
            "module": cap["module_ids"][0] if cap["module_ids"] else "",
            "tool_ids": list(cap["recommended_tool_ids"]),
            "risk_level": risk,
            "can_create_sensitive_output": any("sensitive" in note.lower() for note in cap["safety_notes"]),
            "requires_verification": any(
                word in note.lower()
                for note in cap["safety_notes"]
                for word in ("verify",)
            ),
            "requires_human_review": any(
                word in note.lower()
                for note in cap["safety_notes"]
                for word in ("verify",)
            ),
        }

    return jsonify({
        "capabilities": [project(item) for item in catalog.list_all()],
    })


def _category(capability_id: str) -> str:
    return {
        "knowledge_qa": "knowledge",
        "memory_lookup": "memory",
        "workspace_read": "workspace",
        "report_drafting": "report",
        "runtime_diagnostics": "system",
        "agent_delegation": "agent",
        "browser": "browser",
    }.get(capability_id, capability_id)
