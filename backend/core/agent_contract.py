# backend/api/agent_contract.py
"""Shared helpers for agent transports (HTTP and WebSocket).

Both agent_routes.py and agent_ws.py delegate metadata normalization and
stream mode resolution here so the transport contract stays in one place.
"""

from __future__ import annotations

import json
from typing import Any


def metadata_size(value: Any) -> int:
    """Return the UTF-8 byte size of *value* as JSON."""
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0


def resolve_stream_mode(data: dict) -> tuple[bool, str]:
    """Return (stream_enabled, stream_mode).

    HTTP accepts only the current ``stream_mode`` field. A true ``live`` stream
    requires the WebSocket transport.
    """
    requested = data.get("stream_mode") or False
    if requested is True:
        return True, "event_replay"
    if requested is False or requested is None:
        return False, "sync"
    mode = str(requested).strip().lower()
    if mode in {"1", "true", "yes", "sse", "event_replay", "replay"}:
        return True, "event_replay"
    if mode in {"live", "live_stream"}:
        # HTTP cannot do true live; degrade to event_replay.
        return True, "event_replay"
    return False, "sync"


_STREAM_CONTRACTS = {
    ("http", "event_replay"): "event_replay_after_turn_complete",
    ("http", "sync"): None,
    ("websocket", "live"): "live_stream_via_stream_emitter",
    ("websocket", "event_replay_fallback"): "event_replay_fallback",
}


def normalize_metadata(metadata: dict | None, *, transport: str, stream_mode: str) -> dict:
    """Ensure transport/stream metadata fields are set consistently."""
    if metadata is None or not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)
    metadata.setdefault("transport", transport)
    metadata.setdefault("stream_mode", stream_mode)
    contract = _STREAM_CONTRACTS.get((transport, stream_mode))
    if contract:
        metadata.setdefault("stream_contract", contract)
    return metadata


def normalize_agent_result(result: dict, workspace_id: str) -> dict:
    """Backfill stable message fields on an AgentResult dict.

    This ensures every response — HTTP or WebSocket — exposes the same stable
    field set regardless of which runtime path produced the result.
    """
    result.setdefault("ok", not bool(result.get("error")))
    result.setdefault("workspace_id", workspace_id)
    result.setdefault("run_id", result.get("turn_id") or result.get("request_id") or "")
    result.setdefault("turn_id", result.get("run_id") or result.get("request_id") or "")
    result.setdefault("trace_id", "")
    result.setdefault("intent", "assistant_chat")
    result.setdefault("final_response", "")
    result.setdefault("tool_calls", [])
    result.setdefault("warnings", [])
    result.setdefault("errors", [])
    result.setdefault("metadata", {})
    result.setdefault("report_artifacts", [])
    result.setdefault("artifact_refs", [])
    result.setdefault("trace_available", bool(result.get("trace_id")))
    result.setdefault("timeline_summary", {})
    result.setdefault("memory_written", False)
    result.setdefault("workspace_updated", False)
    result.setdefault("memory_hits_count", 0)
    result.setdefault("knowledge_hits_count", 0)
    result.setdefault("llm", {"enabled": False, "used": False})
    if result.get("trace_id") and not result.get("trace_available"):
        result["trace_available"] = True
    md = result.get("metadata") or {}
    if "memory_hits_count" in md and isinstance(md["memory_hits_count"], int):
        result["memory_hits_count"] = md["memory_hits_count"]
    if "knowledge_hits_count" in md and isinstance(md["knowledge_hits_count"], int):
        result["knowledge_hits_count"] = md["knowledge_hits_count"]
    return result
