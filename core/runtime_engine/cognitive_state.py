"""Bounded, versioned request-local cognitive state for QueryLoop."""
from dataclasses import asdict, dataclass, field
import json
from typing import Any, Iterable, Mapping

from .cognitive_events import (
    COGNITIVE_DECISION_MADE, COGNITIVE_EVIDENCE_REGISTERED,
    COGNITIVE_GAP_DETECTED, COGNITIVE_GOAL_NORMALIZED,
    COGNITIVE_INITIALIZED, COGNITIVE_PLAN_SELECTED, build_cognitive_event,
)

SCHEMA_VERSION = "cognitive-state/v2"
MAX_FACTS = 12
MAX_UNKNOWNS = 8
MAX_EVENTS = 32

def _text(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:limit]

def _texts(value: Iterable[Any], limit: int) -> list[str]:
    return [text for item in list(value)[:limit] if (text := _text(item, 120))]

@dataclass
class CognitiveState:
    turn_id: str
    trace_id: str
    goal: str
    constraints: list[str] = field(default_factory=list)
    completion_criteria: list[str] = field(default_factory=list)
    known_facts: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    decision: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    safety: dict[str, Any] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = "running"
    revision: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def _append(self, event_type: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.revision += 1
        event = build_cognitive_event(
            event_type, turn_id=self.turn_id, trace_id=self.trace_id,
            state_revision=self.revision, payload=dict(payload or {}),
        )
        self.events.append(event)
        self.events = self.events[-MAX_EVENTS:]
        return event

    def add_fact(self, fact: str, *, source: str = "runtime", evidence_id: str = "", verified: bool = True, claim_key: str = "") -> None:
        record = {"fact": _text(fact), "source": _text(source, 80), "evidence_id": _text(evidence_id, 128), "verified": bool(verified)}
        if claim_key:
            record["claim_key"] = _text(claim_key, 120)
        if record["fact"] and record not in self.known_facts:
            self.known_facts = (self.known_facts + [record])[-MAX_FACTS:]

    def add_unknown(
        self,
        item: str,
        *,
        blocking: bool,
        reason: str = "",
        resolution_key: str = "",
    ) -> None:
        record = {"item": _text(item), "blocking": bool(blocking), "reason": _text(reason, 160)}
        if resolution_key:
            record["resolution_key"] = _text(resolution_key, 160)
        if record["item"] and record not in self.unknowns:
            self.unknowns = (self.unknowns + [record])[-MAX_UNKNOWNS:]

    def resolve_unknown(self, resolution_key: str) -> bool:
        """Remove a transient gap once the same logical step succeeds."""
        key = _text(resolution_key, 160)
        if not key:
            return False
        retained = [item for item in self.unknowns if item.get("resolution_key") != key]
        changed = len(retained) != len(self.unknowns)
        self.unknowns = retained
        return changed

    def select_plan(self, steps: Iterable[Mapping[str, Any]], *, reason: str = "") -> None:
        selected: list[dict[str, Any]] = []
        for step in list(steps or [])[:8]:
            action = _text(step.get("action") or step.get("tool"), 120)
            if action:
                selected.append({"action": action, "purpose": _text(step.get("purpose") or step.get("reason"), 180)})
        self.plan = selected
        self._append(COGNITIVE_PLAN_SELECTED, {"criteria": [item["action"] for item in selected], "visible_summary": _text(reason) or f"已选择 {len(selected)} 个执行步骤"})

    def register_tool_results(self, results: Iterable[Any], *, evidence: Mapping[str, Any] | None = None) -> None:
        observed = list(results or [])
        success = 0
        gaps = 0
        uncertain = 0
        conflict_count = 0
        for result in observed:
            tool_id = _text(getattr(result, "tool_name", "") or getattr(result, "tool_id", "") or "tool", 120)
            output = getattr(result, "output", {})
            orchestration = output.get("_orchestration") if isinstance(output, Mapping) else {}
            step_id = _text(
                (orchestration.get("step_id") if isinstance(orchestration, Mapping) else "")
                or getattr(result, "call_id", ""),
                128,
            )
            resolution_key = f"tool_step:{step_id}" if step_id else ""
            summary = _text(getattr(result, "summary", "") or (output.get("summary") if isinstance(output, Mapping) else "") or (output.get("_hint") if isinstance(output, Mapping) else "") or getattr(result, "error", "") or (json.dumps(output, ensure_ascii=False, sort_keys=True, default=str) if isinstance(output, Mapping) else ""), 220)
            if bool(getattr(result, "execution_may_continue", False)):
                uncertain += 1
                self.add_unknown(f"{tool_id} 的写入结果尚未确定", blocking=True, reason="unknown_tool_outcome")
            elif bool(getattr(result, "ok", False)):
                success += 1
                self.resolve_unknown(resolution_key)
                claim_key = _text(output.get("fact_key") or output.get("claim_key") or output.get("evidence_key"), 120) if isinstance(output, Mapping) else ""
                prior = next((item for item in self.known_facts if claim_key and item.get("claim_key") == claim_key and item.get("verified", True)), None)
                if prior is not None and prior.get("fact") != summary:
                    prior["verified"] = False
                    conflict_count += 1
                    self.conflicts = (self.conflicts + [{"claim_key": claim_key, "previous_fact": _text(prior.get("fact"), 180), "current_fact": summary, "previous_evidence_id": _text(prior.get("evidence_id"), 128), "current_evidence_id": _text(getattr(result, "call_id", ""), 128)}])[-MAX_UNKNOWNS:]
                    self.add_unknown(f"证据冲突：{claim_key}", blocking=True, reason="evidence_conflict")
                elif prior is None:
                    self.add_fact(summary or f"{tool_id} 执行成功", source=tool_id, evidence_id=_text(getattr(result, "call_id", ""), 128), claim_key=claim_key)
            else:
                gaps += 1
                self.add_unknown(
                    summary or f"{tool_id} 未成功执行",
                    blocking=False,
                    reason="tool_failure",
                    resolution_key=resolution_key,
                )
        if observed:
            self._append(COGNITIVE_EVIDENCE_REGISTERED, {"fact_count": success - conflict_count, "unknown_count": gaps + uncertain + conflict_count, "conflict_count": conflict_count, "visible_summary": f"已登记 {success} 项有效观察"})
        if gaps or uncertain or conflict_count:
            self._append(COGNITIVE_GAP_DETECTED, {"unknown_count": gaps + uncertain + conflict_count, "blocked_by": "unknown_tool_outcome" if uncertain else ("evidence_conflict" if conflict_count else "tool_failure"), "visible_summary": "部分观察尚不足以支持完成结论"})
        if evidence:
            self.evidence = dict(evidence)
    def set_decision(
        self,
        decision: str,
        *,
        reason_codes: Iterable[str] = (),
        selected_action: str = "",
        visible_summary: str = "",
        **extra: Any,
    ) -> None:
        self.decision = {
            "decision": _text(decision, 80),
            "reason_codes": _texts(reason_codes, 8),
            "selected_action": _text(selected_action, 120),
            "visible_summary": _text(visible_summary),
        }
        for key in ("risk_level", "requires_approval", "expected_observation", "next_action"):
            if key in extra:
                self.decision[key] = extra[key]
        self._append(COGNITIVE_DECISION_MADE, self.decision)

    def set_outcome(
        self,
        outcome: str,
        *,
        reason_codes: Iterable[str] = (),
        visible_summary: str = "",
    ) -> None:
        self.outcome = _text(outcome, 64) or "running"
        self.safety = {
            "stop_reason_codes": _texts(reason_codes, 8),
            "visible_summary": _text(visible_summary),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "goal": self.goal,
            "outcome": self.outcome,
            "known_fact_count": sum(1 for item in self.known_facts if item.get("verified", True)),
            "unknown_count": len(self.unknowns),
            "blocking_unknown_count": sum(1 for item in self.unknowns if item.get("blocking")),
            "conflict_count": len(self.conflicts),
            "plan": list(self.plan),
            "decision": dict(self.decision),
            "evidence": dict(self.evidence),
            "safety": dict(self.safety),
            "visible_summary": _text(self.decision.get("visible_summary") or self.safety.get("visible_summary") or self.goal),
        }

    def as_trace_payload(self) -> dict[str, Any]:
        return asdict(self)


def restore_cognitive_state(
    snapshot: Mapping[str, Any] | None,
    *,
    turn_id: str,
    trace_id: str,
) -> CognitiveState | None:
    """Rehydrate a bounded server-owned continuation snapshot.

    The continuation store is server-only and encrypted, but this function still
    rejects malformed state and bounds every collection before it reaches the
    next prompt projection.
    """
    if not isinstance(snapshot, Mapping):
        return None
    goal = _text(snapshot.get("goal"))
    if not goal:
        return None
    state = CognitiveState(
        turn_id=_text(turn_id, 128),
        trace_id=_text(trace_id, 128),
        goal=goal,
        constraints=_texts(snapshot.get("constraints") or [], 8),
        completion_criteria=_texts(snapshot.get("completion_criteria") or [], 8),
        outcome=_text(snapshot.get("outcome"), 64) or "running",
    )
    for item in list(snapshot.get("known_facts") or [])[:MAX_FACTS]:
        if isinstance(item, Mapping):
            state.add_fact(
                _text(item.get("fact")),
                source=_text(item.get("source"), 80),
                evidence_id=_text(item.get("evidence_id"), 128),
                verified=bool(item.get("verified", True)),
                claim_key=_text(item.get("claim_key"), 120),
            )
    for item in list(snapshot.get("unknowns") or [])[:MAX_UNKNOWNS]:
        if isinstance(item, Mapping):
            state.add_unknown(
                _text(item.get("item")),
                blocking=bool(item.get("blocking")),
                reason=_text(item.get("reason"), 160),
                resolution_key=_text(item.get("resolution_key"), 160),
            )
    state.plan = [
        {"action": _text(item.get("action"), 120), "purpose": _text(item.get("purpose"), 180)}
        for item in list(snapshot.get("plan") or [])[:8]
        if isinstance(item, Mapping) and _text(item.get("action"), 120)
    ]
    for name in ("decision", "evidence", "safety"):
        value = snapshot.get(name)
        if isinstance(value, Mapping):
            setattr(state, name, dict(value))
    state.conflicts = [
        dict(item) for item in list(snapshot.get("conflicts") or [])[:MAX_UNKNOWNS]
        if isinstance(item, Mapping)
    ]
    state.events = [
        dict(item) for item in list(snapshot.get("events") or [])[-MAX_EVENTS:]
        if isinstance(item, Mapping)
    ]
    revisions = [0]
    for value in [snapshot.get("revision")] + [event.get("state_revision") for event in state.events]:
        try:
            revisions.append(max(0, int(value or 0)))
        except (TypeError, ValueError):
            continue
    state.revision = max(revisions)
    return state

def initialize_cognitive_state(
    *,
    turn_id: str,
    trace_id: str,
    user_input: str,
    constraints: Iterable[str] = (),
    completion_criteria: Iterable[str] = (),
) -> CognitiveState:
    goal = _text(user_input) or "完成用户当前请求"
    state = CognitiveState(
        turn_id=_text(turn_id, 128),
        trace_id=_text(trace_id, 128),
        goal=goal,
        constraints=_texts(constraints, 8),
        completion_criteria=_texts(completion_criteria, 8) or ["给出与用户目标一致的可靠结论"],
    )
    state._append(COGNITIVE_INITIALIZED, {"goal": goal, "criteria": state.completion_criteria, "visible_summary": "已建立任务目标和完成标准"})
    state._append(COGNITIVE_GOAL_NORMALIZED, {"goal": goal, "visible_summary": "已规范化当前任务目标"})
    return state
