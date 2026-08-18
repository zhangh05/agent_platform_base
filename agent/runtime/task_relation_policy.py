"""Server-owned classification for bounded task-continuation relations.

The classifier returns only relation mechanics. User wording remains ordinary
conversation data and is never copied into trusted prompt channels.
"""
from __future__ import annotations

import re
from typing import Any

_APPEND = re.compile(
    r"^(?:再来|再给|再生成|再写|再列|再补)\s*"
    r"(?:(?P<count>\d+)\s*)?(?P<unit>条|个|项|份|段|组)?"
    r"(?P<tail>.*)$"
)
# A qualified append may carry only an explicit continuity constraint after the
# quantity. This keeps “再来2条，保持 PARK- 前缀和连续编号” on the active
# deliverable while refusing “再来2条，分析杭州天气” as a new topic.
_APPEND_CONTINUITY_TAIL = re.compile(
    r"^[，,、;；:：\s]*(?:"
    r"(?:保持|沿用|继续|仍用|按照|按|使用|采用|格式|前缀|编号|序号|范围|不变|一致|"
    r"之前|原有|上文|上述|以上|每条|同样).{0,120}"
    r")?[。.!！?？\s]*$"
)
_EXPAND = re.compile(r"^(?:继续|接着|展开|详细点|再详细|再说说)[。.!！?？\s]*$")
_ITEM_COUNT = re.compile(r"(?<!\d)(?P<count>\d{1,3})\s*(?P<unit>条|个|项|份|段|组)")
_OPERATION = re.compile(
    r"(?P<rewrite>重写|改写|润色|改版|换成|改成|改为)|"
    r"(?P<scope>删除|删掉|去掉|移除|剔除|只保留|只输出|不包括|排除)|"
    r"(?P<repair>补充|补齐|补漏|完善|修复|纠正|更正)|"
    r"(?P<summarize>总结|汇总|归纳|收敛|提炼)|"
    r"(?P<refine>优化|改进|调整)"
)


def _bounded_count(value: str) -> tuple[int | None, str]:
    match = _ITEM_COUNT.search(value)
    if not match:
        return None, ""
    count = int(match.group("count") or 0)
    return (count if 0 < count <= 200 else None), str(match.group("unit") or "")


def classify_task_relation(user_input: str) -> dict[str, Any] | None:
    """Classify an explicit follow-up instruction into a bounded relation type."""
    value = str(user_input or "").strip()
    append = _APPEND.fullmatch(value)
    if append:
        tail = str(append.group("tail") or "")
        if len(value) > 240 or not _APPEND_CONTINUITY_TAIL.fullmatch(tail):
            return None
        count = int(append.group("count") or 0)
        if count < 0 or count > 200:
            return None
        return {"kind": "append", "expected_new_items": count or None, "unit": append.group("unit") or ""}
    if _EXPAND.fullmatch(value):
        return {"kind": "expand", "instruction_present": False}
    operation = _OPERATION.search(value)
    if not operation or len(value) > 240:
        return None
    kind = next((name for name, matched in operation.groupdict().items() if matched), "")
    if not kind:
        return None
    relation: dict[str, Any] = {"kind": kind, "instruction_present": True}
    if kind == "scope":
        count, unit = _bounded_count(value)
        if count:
            relation["target_item_count"] = count
            relation["unit"] = unit
    return relation


def render_task_relation_guidance(relation: dict[str, Any]) -> str:
    """Render server-owned semantics for a typed relation without user prose."""
    kind = str(relation.get("kind") or "")
    if kind in {"", "append"}:
        return ""
    operation = {
        "expand": "expand the active deliverable without replacing its identity",
        "rewrite": "rework the active deliverable according to the current user instruction",
        "scope": "apply the current scope restriction to the active deliverable",
        "repair": "repair or complete the active deliverable according to the current user instruction",
        "summarize": "compress the active deliverable while preserving its grounded conclusions",
        "refine": "improve the active deliverable according to the current user instruction",
    }.get(kind)
    if not operation:
        return ""
    suffix = ""
    if relation.get("target_item_count"):
        suffix = " The server-derived target item count is " + str(int(relation.get("target_item_count") or 0)) + "."
    return (
        "relation_operation=" + kind + "\n"
        "Apply this operation only to the active task; do not create an unrelated deliverable. "
        "The current user instruction supplies the requested semantic change, while task identity and prior delivery mechanics remain server-owned. "
        + operation + "." + suffix
    )
