"""Deterministic plan enrichment for safe, read-only omissions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlanEnrichment:
    node_id: str
    tool: str
    field: str
    value: Any
    reason: str


def enrich_tool_calls_from_user_request(nodes: list[Any], user_input: str) -> list[PlanEnrichment]:
    """Enrich normalized QueryLoop calls with safe inferred parameters."""
    text = str(user_input or "")
    events: list[PlanEnrichment] = []
    for node in nodes:
        if getattr(node, "tool", "") == "web.manage":
            events.extend(_enrich_weather_node(node, text))
    return events


def enrich_model_calls_from_user_request(nodes: list[Any], user_input: str) -> list[PlanEnrichment]:
    """Mutate model tool calls before execution for intent-level repairs.

    The base only performs domain-neutral enrichment. Today that means coercing
    clear weather requests from generic search into the structured weather
    action, and inferring location/horizon when present in user text.
    """
    text = str(user_input or "")
    events: list[PlanEnrichment] = []
    for node in nodes:
        if getattr(node, "tool", "") == "web.manage":
            events.extend(_coerce_weather_action(node, text))
    return events


def _coerce_weather_action(node, text: str) -> list[PlanEnrichment]:
    args = getattr(node, "args", None)
    if not isinstance(args, dict):
        return []
    action = str(args.get("action") or "").lower()
    if action == "weather":
        return []
    if action != "search" or not _mentions_weather(text):
        return []
    args["action"] = "weather"
    location = infer_weather_location(text)
    days = infer_weather_days(text)
    if location:
        args["location"] = location
    if days:
        args["days"] = days
    return [
        PlanEnrichment(
            node_id=getattr(node, "id", ""),
            tool="web.manage",
            field="action",
            value="weather",
            reason="weather_request_should_use_structured_weather",
        )
    ]


def _enrich_weather_node(node, text: str) -> list[PlanEnrichment]:
    args = getattr(node, "args", None)
    if not isinstance(args, dict):
        return []
    if str(args.get("action") or "").lower() != "weather":
        return []

    events: list[PlanEnrichment] = []
    inferred_days = infer_weather_days(text)
    if inferred_days and int(args.get("days") or 1) < inferred_days:
        args["days"] = inferred_days
        events.append(PlanEnrichment(
            node_id=getattr(node, "id", ""),
            tool="web.manage",
            field="days",
            value=inferred_days,
            reason="weather_horizon_from_user_text",
        ))

    if not str(args.get("location") or "").strip():
        location = infer_weather_location(text)
        if location:
            args["location"] = location
            events.append(PlanEnrichment(
                node_id=getattr(node, "id", ""),
                tool="web.manage",
                field="location",
                value=location,
                reason="weather_location_from_user_text",
            ))
    return events


def _mentions_weather(text: str) -> bool:
    t = str(text or "").lower()
    return any(w in t for w in ("天气", "气温", "温度", "预报", "weather", "forecast"))


def infer_weather_days(text: str) -> int | None:
    """Infer weather forecast days from Chinese/English date wording."""
    t = str(text or "").lower()
    if not t:
        return None
    if "后天" in t:
        return 3
    if "明天" in t or "tomorrow" in t:
        return 2
    if "一周" in t or "7天" in t or "七天" in t or "week" in t:
        return 7
    m = re.search(r"(?:未来|后续|接下来|future|next)?\s*(\d{1,2})\s*(?:天|day|days)", t)
    if m:
        return max(1, min(int(m.group(1)), 10))
    cn = {
        "十": 10, "九": 9, "八": 8, "七": 7, "六": 6,
        "五": 5, "四": 4, "三": 3, "两": 2, "二": 2, "一": 1,
    }
    m = re.search(r"(?:未来|后续|接下来)?\s*([一二两三四五六七八九十])\s*天", t)
    if m:
        return cn.get(m.group(1))
    if "未来" in t or "预报" in t or "forecast" in t:
        return 3
    return None


def infer_weather_location(text: str) -> str:
    """Extract a compact location hint for weather requests."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"(今天|明天|后天|未来|接下来|天气|气温|温度|预报|weather|forecast|怎么样|如何|查询|帮我|看一下)", " ", raw, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。.!！？?：:")
    return cleaned[:80]
