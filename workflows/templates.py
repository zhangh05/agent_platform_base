"""Domain-neutral registry for extension-owned workflow templates."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from storage.time_utils import now_iso


def _templates() -> tuple[dict, ...]:
    from extensions.runtime import load_extensions

    return tuple(
        deepcopy(template)
        for extension in load_extensions()
        for template in extension.workflow_templates
    )


def list_workflow_templates() -> list[dict[str, Any]]:
    """Return public metadata only; definitions stay server-managed."""
    return [
        {key: deepcopy(value) for key, value in template.items() if key != "definition"}
        for template in _templates()
    ]


def get_workflow_template(template_id: str) -> dict[str, Any] | None:
    key = str(template_id or "").strip()
    for template in _templates():
        if template["template_id"] == key:
            return deepcopy(template)
    return None


def instantiate_workflow_template(workspace_id: str, template_id: str, *, name: str = "") -> dict[str, Any]:
    """Create a durable workflow via the canonical workflow save service."""
    template = get_workflow_template(template_id)
    if not template:
        raise ValueError("workflow_template_not_found")
    from workflows.service import save_workflow
    suffix = f"{now_iso().replace("-", "").replace(":", "").replace("+", "").replace("T", "")[:14]}-{uuid4().hex[:8]}"
    definition = deepcopy(template["definition"])
    workflow = save_workflow(workspace_id, {
        "workflow_id": f"{template_id}-{suffix}",
        "template_id": template_id,
        "name": str(name or template["name"]).strip()[:120] or template["name"],
        "description": template["description"],
        "version": 1,
        "status": "active",
        **definition,
    })
    return {"workflow": workflow, "template": {key: value for key, value in template.items() if key != "definition"}}
