# observability/timeline.py
"""Trace timeline — node-level timing and summary."""

import time
from observability.schemas import TraceEvent

# Canonical node display names
CANONICAL_NODE_NAMES = {"router", "context_loader", "planner", "executor",
                         "verifier", "composer", "memory_writer"}


class NodeTimer:
    """Context manager for timing a node and emitting trace events."""

    def __init__(self, state, node_name: str, trace_id: str, workspace_id: str = "default"):
        self._state = state
        self._name = node_name
        self._trace_id = trace_id
        self._workspace_id = workspace_id
        self._start = 0.0
        self._start_event = None

    def __enter__(self):
        self._start = time.time()
        evt = TraceEvent(
            trace_id=self._trace_id,
            run_id=self._state.request_id,
            workspace_id=self._workspace_id,
            event_type="node_start",
            name=self._name,
            status="started",
        )
        self._start_event = evt.as_dict()
        self._state.trace_events.append(self._start_event)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = round((time.time() - self._start) * 1000, 2)
        status = "success" if exc_type is None else "failed"
        evt = TraceEvent(
            trace_id=self._trace_id,
            run_id=self._state.request_id,
            workspace_id=self._workspace_id,
            event_type="node_end",
            name=self._name,
            status=status,
            duration_ms=duration,
            summary=f"{self._name}: {status} ({duration}ms)",
        )
        if exc_val:
            evt.summary += f" | error: {str(exc_val)[:100]}"
        self._state.trace_events.append(evt.as_dict())
        self._state.node_timings[self._name] = duration
        return False  # don't suppress exceptions


def add_event(state, event_type: str, name: str, status: str = "started",
              trace_id: str = "", summary: str = "", metadata: dict = None,
              duration_ms: float = 0.0):
    """Add a trace event to state."""
    workspace_id = getattr(state, "workspace_id", "") or ""
    if not workspace_id:
        raise ValueError("workspace_id is required for trace events")
    evt = TraceEvent(
        trace_id=trace_id,
        run_id=state.request_id,
        workspace_id=workspace_id,
        event_type=event_type,
        name=name,
        status=status,
        summary=summary or f"{event_type}: {name}",
        metadata=metadata or {},
        duration_ms=duration_ms,
    )
    state.trace_events.append(evt.as_dict())
    return evt


def build_timeline_summary(state) -> dict:
    """Build a timeline summary from state trace events.

    All counts are derived from trace events, never hardcoded.
    node_count only counts the canonical 7 node_end events.
    """
    events = state.trace_events
    if not events:
        return {
            "total_duration_ms": 0, "node_count": 0,
            "capability_call_count": 0, "module_call_count": 0,
            "llm_call_count": 0, "memory_write_count": 0,
            "warning_count": 0, "error_count": 0,
        }

    # Only count canonical node_end events (excludes capability/module/llm sub-events)
    node_count = sum(
        1 for e in events
        if e.get("event_type") == "node_end"
        and e.get("name") in CANONICAL_NODE_NAMES
    )

    cap_count = sum(1 for e in events if e.get("event_type") == "capability_call_end")
    module_count = sum(1 for e in events if e.get("event_type") == "module_call_end")

    # llm_call_count: count all llm_call_end events (success, skipped, failed)
    llm_count = sum(1 for e in events if e.get("event_type") == "llm_call_end")

    mem_count = sum(1 for e in events if e.get("event_type") == "memory_write")
    warn_count = sum(1 for e in events if e.get("event_type") == "warning")
    err_count = sum(1 for e in events if e.get("event_type") == "error")

    total_ms = sum(e.get("duration_ms", 0) for e in events
                   if e.get("event_type") == "node_end"
                   and e.get("name") in CANONICAL_NODE_NAMES)

    return {
        "total_duration_ms": round(total_ms, 2),
        "node_count": node_count,
        "capability_call_count": cap_count,
        "module_call_count": module_count,
        "llm_call_count": llm_count,
        "memory_write_count": mem_count,
        "warning_count": warn_count,
        "error_count": err_count,
        "artifact_saved_count": sum(1 for e in events if e.get("event_type") == "artifact_saved"),
        "artifact_read_count": sum(1 for e in events if e.get("event_type") == "artifact_read"),
    }
