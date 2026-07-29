# observability/store.py
"""Trace store — write/read trace records to workspace runs directory."""

from typing import Optional

from storage.records import atomic_save_json, list_json_records, read_json_record


def write_trace(trace, ws_id: str = "default") -> str:
    """Write a trace record to workspace runs directory. Returns trace_id."""
    from storage.ids import validate_workspace_id
    ws_id = validate_workspace_id(ws_id)

    trace_id = trace.trace_id
    run_id = trace.run_id

    # Redact before writing
    from observability.redaction import redact_trace
    data = redact_trace(trace.as_dict())

    atomic_save_json(ws_id, ("runs", f"{run_id}.trace.json"), data)

    return trace_id


def get_trace(run_id: str, ws_id: str = "default") -> Optional[dict]:
    """Get a trace record by run_id."""
    from storage.ids import validate_workspace_id
    ws_id = validate_workspace_id(ws_id)
    return read_json_record(ws_id, ("runs", f"{run_id}.trace.json"))


def list_traces(ws_id: str = "default", limit: int = 50) -> list:
    """List trace records for a workspace."""
    from storage.ids import validate_workspace_id
    ws_id = validate_workspace_id(ws_id)
    traces = []
    for trace in list_json_records(ws_id, ("runs",), limit=limit):
        try:
            traces.append({
                "trace_id": trace.get("trace_id", ""),
                "run_id": trace.get("run_id", ""),
                "workspace_id": trace.get("workspace_id", ""),
                "status": trace.get("status", ""),
                "total_duration_ms": trace.get("total_duration_ms", 0),
                "node_count": trace.get("node_count", 0),
                "event_count": len(trace.get("events", [])),
            })
            if len(traces) >= limit:
                break
        except Exception:
            pass
    return traces


def append_event(trace_id: str, event, ws_id: str = "default"):
    """Append an event to an existing trace.

    Performs an O(n) lookup via filename-derived hint first, falling
    back to a single directory scan if the hint path is missing.
    Writes are serialized by the storage record adapter.
    """
    from storage.ids import validate_workspace_id
    ws_id = validate_workspace_id(ws_id)
    # Cheap filename-derived hint: most callers know run_id, so
    # try that path first to avoid scanning the whole runs dir.
    data = None
    if hasattr(event, "run_id") and getattr(event, "run_id", None):
        candidate = read_json_record(ws_id, ("runs", f"{event.run_id}.trace.json"))
        if candidate and candidate.get("trace_id") == trace_id:
            data = candidate

    if data is None:
        for trace in list_json_records(ws_id, ("runs",), limit=500):
            if trace.get("trace_id") == trace_id:
                data = trace
                break

    if data is None:
        return

    try:
        from observability.redaction import redact_trace_event
        events = data.get("events", [])
        event_data = event.as_dict() if hasattr(event, "as_dict") else event
        events.append(redact_trace_event(event_data))
        data["events"] = events
        run_id = data.get("run_id") or getattr(event, "run_id", "")
        if not run_id:
            return
        atomic_save_json(ws_id, ("runs", f"{run_id}.trace.json"), data)
    except Exception:
        pass
