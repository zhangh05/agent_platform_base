"""Single-source business capability catalog for LZCore.

A "business capability" is a thin description of a thing the agent can
do, plus a list of recommended tool ids. It is NOT a tool registration
mechanism, NOT a visibility gate, NOT a permission/authorization layer —
those concerns live in canonical_registry / manifest_registry / sandbox.

Three rules:
  1. recommended_tool_ids MUST be a subset of TOOL_NAMESPACE (the
     canonical tool ids, see ``core.tools.tool_namespace``). Removed
     names like device.list / git.status are invalid.
  2. This module exposes only data + a few lookup helpers. It does not
     register tools, filter tools, or influence dispatch.
  3. Frontend (skills / capabilities API) and the skill.manage tool
     read this catalog directly. There is no second source of truth.
"""

from __future__ import annotations

from typing import Any, Iterable

from core.tools.tool_namespace import TOOL_NAMESPACE


# Field schema for each entry:
#   capability_id       str   unique id
#   display_name        str   human-readable name
#   description         str   one-sentence description
#   module_ids          tuple  backend module name(s)
#   recommended_tool_ids tuple  canonical tool ids from the platform tool set
#   prompt_hints        tuple  short hints for the LLM when invoking
#   safety_notes        tuple  short safety warnings for the LLM
#   status              str   always "enabled" for current capabilities
_CAPABILITIES: tuple[dict, ...] = (
    {
        "capability_id": "workspace_read",
        "display_name": "工作区读取",
        "description": "读取并检查当前用户工作区中的文件与任务产物。",
        "module_ids": ("workspace",),
        "recommended_tool_ids": ("workspace.file", "workspace.artifact"),
        "prompt_hints": ("分析领域内容前，先读取工作区中的原始文件。",),
        "safety_notes": (),
        "status": "enabled",
    },
    {
        "capability_id": "knowledge_qa",
        "display_name": "知识问答",
        "description": "检索并读取已经建立索引的知识内容。",
        "module_ids": ("knowledge",),
        "recommended_tool_ids": ("knowledge.manage",),
        "prompt_hints": (),
        "safety_notes": (),
        "status": "enabled",
    },
    {
        "capability_id": "memory_lookup",
        "display_name": "记忆检索",
        "description": "检索当前用户的长期记忆、偏好和已确认事实。",
        "module_ids": ("memory",),
        "recommended_tool_ids": ("memory.manage",),
        "prompt_hints": (),
        "safety_notes": (),
        "status": "enabled",
    },
    {
        "capability_id": "report_drafting",
        "display_name": "报告生成",
        "description": "生成结构化报告，并保存为可下载的任务产物。",
        "module_ids": ("workspace",),
        "recommended_tool_ids": ("report.manage", "workspace.artifact"),
        "prompt_hints": (),
        "safety_notes": (),
        "status": "enabled",
    },
    {
        "capability_id": "runtime_diagnostics",
        "display_name": "运行检查",
        "description": "检查平台运行状态、健康信息和诊断记录。",
        "module_ids": ("runtime",),
        "recommended_tool_ids": ("system.manage",),
        "prompt_hints": (),
        "safety_notes": (),
        "status": "enabled",
    },
    {
        "capability_id": "location_resolution",
        "display_name": "位置解析",
        "description": "将地点、地址和坐标解析为带来源、行政层级、候选与置信度的标准位置实体。",
        "module_ids": ("location",),
        "recommended_tool_ids": ("location.manage",),
        "prompt_hints": (
            "位置会影响后续查询或操作时先解析；重名候选未消歧时不得猜测。",
            "批量地点使用 resolve_batch，并核对请求、成功和未解析集合。",
        ),
        "safety_notes": (
            "政策或业务区域的成员范围必须来自明确口径，不能由地理编码器自行定义。",
        ),
        "status": "enabled",
    },
    {
        "capability_id": "agent_delegation",
        "display_name": "智能体协作",
        "description": "派发独立子任务、组织协作并汇总执行结果。",
        "module_ids": ("runtime",),
        "recommended_tool_ids": (
            "agent.manage",
        ),
        "prompt_hints": (
            "仅在任务确实需要独立调查时派发子任务，并在完成后读取结果。",
        ),
        "safety_notes": (
            "子智能体继承当前用户、工作区和会话边界。",
            "简单的一步查询不应派发子智能体。",
        ),
        "status": "enabled",
    },
    {
        "capability_id": "browser",
        "display_name": "浏览器操作",
        "description": "打开网页并执行导航、内容提取、截图和点击。",
        "module_ids": ("browser",),
        "recommended_tool_ids": ("browser.manage",),
        "prompt_hints": (
            "需要与网页交互时使用浏览器操作；只需检索公开信息时优先使用网络搜索。",
        ),
        "safety_notes": (
            "网页内容来自外部站点；未经授权不得访问内部或需要登录的地址。"
        ),
        "status": "enabled",
    },
)


# v3.9.4 contract check at import time: every recommended_tool_id MUST be
# a canonical id. This guarantees the catalog cannot accidentally
# re-introduce a removed tool name.
def _validate_catalog() -> None:
    canonical = set(TOOL_NAMESPACE)
    for cap in _CAPABILITIES:
        for tid in cap["recommended_tool_ids"]:
            if tid not in canonical:
                raise ValueError(
                    f"business capability {cap['capability_id']!r} references "
                    f"non-canonical tool id {tid!r}; expected one of {sorted(canonical)}"
                )


_validate_catalog()

# Public immutable catalog export for code that needs a data handle rather
# than helper functions. Keep helpers as the preferred read API.
CAPABILITY_CATALOG: tuple[dict, ...] = _CAPABILITIES


def list_all() -> list[dict]:
    """Return the current business capabilities."""
    return list(CAPABILITY_CATALOG)


def list_enabled() -> list[dict]:
    """Return enabled business capabilities only."""
    return [c for c in CAPABILITY_CATALOG if c["status"] == "enabled"]


def get(capability_id: str) -> dict | None:
    for c in CAPABILITY_CATALOG:
        if c["capability_id"] == capability_id:
            return c
    return None


def to_skill_dict(cap: dict) -> dict:
    """Render a business capability as the skill.manage dict shape."""
    return {
        "skill_id": cap["capability_id"],
        "display_name": cap["display_name"],
        "description": cap["description"],
        "status": cap["status"],
        "capability_ids": (cap["capability_id"],),
        "module_ids": tuple(cap["module_ids"]),
        "tool_ids": tuple(cap["recommended_tool_ids"]),
        "prompt_hints": tuple(cap["prompt_hints"]),
        "safety_notes": tuple(cap["safety_notes"]),
        "source": "business_capability_catalog",
    }


def all_recommended_tool_ids() -> set[str]:
    """Set of every tool_id that any business capability recommends."""
    out: set[str] = set()
    for c in CAPABILITY_CATALOG:
        out.update(c["recommended_tool_ids"])
    return out


__all__ = [
    "CAPABILITY_CATALOG",
    "list_all",
    "list_enabled",
    "get",
    "to_skill_dict",
    "all_recommended_tool_ids",
]
