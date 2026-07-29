"""Trace redaction — strip secrets before persisting trace data."""

from storage.redaction import contains_secret, redact_dict, redact_text


def redact_trace_event(event: dict) -> dict:
    """Redact sensitive data from a trace event."""
    if not event:
        return event
    result = dict(event)
    has_secret = False

    # Redact metadata
    if "metadata" in result:
        if contains_secret(str(result["metadata"])):
            has_secret = True
        result["metadata"] = redact_dict(result["metadata"]) if isinstance(result["metadata"], dict) else redact_text(str(result["metadata"]))

    # Redact summary
    if "summary" in result and contains_secret(str(result["summary"])):
        has_secret = True
        result["summary"] = redact_text(str(result["summary"]))

    # Strip full configs from metadata
    for key in ("raw_source", "raw_output", "raw_payload", "config"):
        if key in result.get("metadata", {}):
            result["metadata"][key] = "[REDACTED_FULL_CONFIG]"
            has_secret = True

    result["redaction_applied"] = has_secret or result.get("redaction_applied", False)
    return result


def redact_trace(trace: dict) -> dict:
    """Redact a full trace record."""
    result = dict(trace)
    has_secret = False

    for event in result.get("events", []):
        redacted = redact_trace_event(event)
        events_list = result.setdefault("events", [])
        idx = events_list.index(event) if event in events_list else -1
        if idx >= 0:
            events_list[idx] = redacted
        if redacted.get("redaction_applied"):
            has_secret = True

    result["redaction_applied"] = has_secret or result.get("redaction_applied", False)
    return result
