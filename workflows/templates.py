"""Curated workflow templates for user-facing, repeatable operations."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from storage.time_utils import now_iso


_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "template_id": "network-asset-inventory",
        "name": "网络资产清单核对",
        "description": "读取当前工作区已登记的网络设备资产，用于巡检前确认目标范围。",
        "audience": "网络运维",
        "expected_result": "返回已登记资产列表；不会连接设备或修改任何配置。",
        "input_example": {},
        "definition": {
            "failure_policy": "fail_fast",
            "nodes": [{
                "node_id": "list_assets",
                "name": "读取网络资产",
                "tool_id": "network.operations.assets_read",
                "arguments": {"action": "list"},
                "depends_on": [],
            }],
        },
    },
    {
        "template_id": "network-readonly-inspection",
        "name": "批量只读网络巡检",
        "description": "对指定的已登记设备发起只读 SSH 巡检任务，不会下发配置。",
        "audience": "网络运维",
        "expected_result": "创建可追踪的巡检任务；请在“网络巡检”页面查看设备级证据和最终结果。",
        "input_example": {"asset_ids": ["填写已登记的设备 ID"]},
        "definition": {
            "failure_policy": "fail_fast",
            "nodes": [{
                "node_id": "start_inspection",
                "name": "发起只读巡检",
                "tool_id": "network.operations.inspection",
                "arguments": {"action": "run", "asset_ids": "${input.asset_ids}"},
                "depends_on": [],
            }],
        },
    },
)


def list_workflow_templates() -> list[dict[str, Any]]:
    """Return public metadata only; definitions stay server-managed."""
    return [{key: deepcopy(value) for key, value in template.items() if key != "definition"} for template in _TEMPLATES]


def get_workflow_template(template_id: str) -> dict[str, Any] | None:
    key = str(template_id or "").strip()
    for template in _TEMPLATES:
        if template["template_id"] == key:
            return deepcopy(template)
    return None


def instantiate_workflow_template(workspace_id: str, template_id: str, *, name: str = "") -> dict[str, Any]:
    """Create a durable workflow via the canonical workflow save service."""
    template = get_workflow_template(template_id)
    if not template:
        raise ValueError("workflow_template_not_found")
    from workflows.service import save_workflow
    suffix = now_iso().replace("-", "").replace(":", "").replace("+", "").replace("T", "")[:14]
    definition = deepcopy(template["definition"])
    workflow = save_workflow(workspace_id, {
        "workflow_id": f"{template_id}-{suffix}",
        "name": str(name or template["name"]).strip()[:120] or template["name"],
        "description": template["description"],
        "version": 1,
        "status": "active",
        **definition,
    })
    return {"workflow": workflow, "template": {key: value for key, value in template.items() if key != "definition"}}
