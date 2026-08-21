"""Workflow templates owned by the network.operations extension."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "template_id": "network-operations-asset-inventory",
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
                "arguments": {},
                "depends_on": [],
            }],
        },
    },
    {
        "template_id": "network-operations-readonly-inspection",
        "name": "批量只读网络巡检",
        "description": "对指定的已登记设备发起只读 SSH 巡检任务，不会下发配置。",
        "audience": "网络运维",
        "expected_result": "创建可追踪的巡检任务；请在“网络巡检”页面查看设备级证据和最终结果。",
        "input_example": {"asset_ids": ["选择已登记设备"], "script_id": "选择巡检脚本"},
        "definition": {
            "failure_policy": "fail_fast",
            "nodes": [{
                "node_id": "start_inspection",
                "name": "发起只读巡检",
                "tool_id": "network.operations.inspection",
                "arguments": {"action": "run", "asset_ids": "${input.asset_ids}", "script_id": "${input.script_id}"},
                "depends_on": [],
            }],
        },
    },
)


def workflow_templates() -> tuple[dict[str, Any], ...]:
    return tuple(deepcopy(template) for template in _TEMPLATES)
