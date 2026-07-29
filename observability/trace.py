# observability/trace.py
"""Trace top-level — create, finalize, query traces."""

import time
import uuid

from agent.runtime.utils import now_iso, from_iso
from observability.schemas import TraceRecord
from observability.timeline import build_timeline_summary


def create_trace(state, ws_id: str = "default") -> str:
    """Create a trace and attach trace_id to state. Returns trace_id."""
    trace_id = str(uuid.uuid4())[:12]
    state.trace_id = trace_id
    state.trace_events = []
    state.node_timings = {}

    start_evt = {
        "event_id": str(uuid.uuid4())[:8],
        "trace_id": trace_id,
        "run_id": state.request_id,
        "workspace_id": ws_id,
        "event_type": "agent_start",
        "name": "agent_start",
        "status": "started",
        "timestamp": state.created_at,
        "duration_ms": 0.0,
        "summary": f"Agent run started: {state.intent or state.user_input[:50]}",
        "metadata": {},
        "redaction_applied": False,
    }
    state.trace_events.append(start_evt)

    return trace_id


def finalize_trace(state, ws_id: str = "default") -> TraceRecord:
    """Finalize a trace record from state events. Returns TraceRecord."""
    finished_at = now_iso()
    duration = round((time.time() - _parse_time(state.created_at)) * 1000, 2) if state.created_at else 0.0

    summary = build_timeline_summary(state)
    result = state.skill_results or state.tool_results or {}
    summary["quality_summary"] = _safe_quality_summary(result)

    end_evt = {
        "event_id": str(uuid.uuid4())[:8],
        "trace_id": state.trace_id or "",
        "run_id": state.request_id,
        "workspace_id": ws_id,
        "event_type": "agent_end",
        "name": "agent_end",
        "status": "failed" if state.error else "success",
        "timestamp": finished_at,
        "duration_ms": duration,
        "summary": f"Agent run finished: {state.intent} | total={duration}ms",
        "metadata": summary,
        "redaction_applied": False,
    }
    state.trace_events.append(end_evt)

    trace = TraceRecord(
        trace_id=state.trace_id or "",
        run_id=state.request_id,
        workspace_id=ws_id,
        request_id=state.request_id,
        started_at=state.created_at,
        finished_at=finished_at,
        status="failed" if state.error else "success",
        total_duration_ms=summary["total_duration_ms"] + duration,
        events=list(state.trace_events),
        node_count=summary["node_count"],
        capability_call_count=summary["capability_call_count"],
        module_call_count=summary["module_call_count"],
        llm_call_count=summary["llm_call_count"],
        memory_write_count=summary["memory_write_count"],
        warning_count=summary["warning_count"],
        error_count=summary["error_count"],
    )

    return trace


def _parse_time(ts: str) -> float:
    """Parse ISO timestamp to epoch seconds."""
    try:
        return from_iso(ts)
    except Exception:
        return time.time()


def _safe_quality_summary(result: dict) -> dict:
    qs = result.get("quality_summary", {}) if isinstance(result, dict) else {}
    keys = [
        "source_residue_count",
        "silent_drop_count",
        "unsupported_count",
        "safe_drop_count",
        "review_required_count",
    ]
    return {key: int(qs.get(key, 0) or 0) if isinstance(qs, dict) else 0 for key in keys}
