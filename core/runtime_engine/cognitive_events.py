"""Server-owned, bounded cognitive decision events."""
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

COGNITIVE_INITIALIZED = "cognitive_initialized"
COGNITIVE_GOAL_NORMALIZED = "cognitive_goal_normalized"
COGNITIVE_PLAN_SELECTED = "cognitive_plan_selected"
COGNITIVE_EVIDENCE_REGISTERED = "cognitive_evidence_registered"
COGNITIVE_GAP_DETECTED = "cognitive_gap_detected"
COGNITIVE_DECISION_MADE = "cognitive_decision_made"
COGNITIVE_STOP_DECIDED = "cognitive_stop_decided"

COGNITIVE_EVENT_TYPES = frozenset({
    COGNITIVE_INITIALIZED, COGNITIVE_GOAL_NORMALIZED, COGNITIVE_PLAN_SELECTED,
    COGNITIVE_EVIDENCE_REGISTERED, COGNITIVE_GAP_DETECTED, COGNITIVE_DECISION_MADE,
    COGNITIVE_STOP_DECIDED,
})
_TEXT_KEYS = frozenset({"decision", "selected_action", "expected_observation", "risk_level", "visible_summary", "outcome", "goal", "blocked_by", "next_action"})
_LIST_KEYS = frozenset({"reason_codes", "criteria"})
_INT_KEYS = frozenset({"fact_count", "unknown_count", "conflict_count", "revision"})

def _text(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:limit]

def _list(value: Any) -> list[str]:
    items = value if isinstance(value, (list, tuple, set)) else []
    return [item for entry in list(items)[:8] if (item := _text(entry, 96))]

def build_cognitive_event(event_type: str, *, turn_id: str, trace_id: str, state_revision: int, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if event_type not in COGNITIVE_EVENT_TYPES:
        raise ValueError(f"unsupported cognitive event type: {event_type}")
    source = dict(payload or {})
    safe: dict[str, Any] = {}
    for key in _TEXT_KEYS:
        if key in source and (value := _text(source[key])):
            safe[key] = value
    for key in _LIST_KEYS:
        if key in source:
            safe[key] = _list(source[key])
    for key in _INT_KEYS:
        if key in source:
            try:
                safe[key] = max(0, int(source[key]))
            except (TypeError, ValueError):
                pass
    return {
        "event_id": f"cog-{uuid4().hex}", "type": event_type,
        "turn_id": _text(turn_id, 128), "trace_id": _text(trace_id, 128),
        "state_revision": max(0, int(state_revision)), "payload": safe,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
