# observability/schemas.py
"""TraceEvent, TraceRecord schemas for runtime observability."""

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.runtime.utils import now_iso

EVENT_TYPES = {
    "agent_start", "agent_end",
    "node_start", "node_end",
    "intent_routed", "context_loaded", "plan_created",
    "capability_call_start", "capability_call_end",
    "module_call_start", "module_call_end",
    "verification",
    "llm_call_start", "llm_call_end",
    "memory_write", "workspace_update", "artifact_saved",
    "run_record_write",
    "tool_runtime",
    "warning", "error",
}

STATUS_VALUES = {"started", "success", "failed", "skipped"}


def _utcnow() -> str:
    return now_iso()


@dataclass
class TraceEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    trace_id: str = ""
    run_id: str = ""
    workspace_id: str = ""
    event_type: str = ""            # see EVENT_TYPES
    name: str = ""                  # human-readable: "router", "context_loader", etc.
    status: str = "started"         # started | success | failed | skipped
    timestamp: str = field(default_factory=_utcnow)
    duration_ms: float = 0.0
    summary: str = ""
    metadata: dict = field(default_factory=dict)
    redaction_applied: bool = False

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "event_type": self.event_type,
            "name": self.name,
            "status": self.status,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "metadata": self.metadata,
            "redaction_applied": self.redaction_applied,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TraceEvent":
        return cls(
            event_id=data.get("event_id", ""),
            trace_id=data.get("trace_id", ""),
            run_id=data.get("run_id", ""),
            workspace_id=data.get("workspace_id", ""),
            event_type=data.get("event_type", ""),
            name=data.get("name", ""),
            status=data.get("status", "started"),
            timestamp=data.get("timestamp", ""),
            duration_ms=data.get("duration_ms", 0.0),
            summary=data.get("summary", ""),
            metadata=data.get("metadata", {}),
            redaction_applied=data.get("redaction_applied", False),
        )


@dataclass
class TraceRecord:
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    run_id: str = ""
    workspace_id: str = ""
    request_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    status: str = "started"
    total_duration_ms: float = 0.0
    events: list = field(default_factory=list)
    node_count: int = 0
    capability_call_count: int = 0
    module_call_count: int = 0
    llm_call_count: int = 0
    memory_write_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    redaction_applied: bool = False

    def as_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "request_id": self.request_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "total_duration_ms": self.total_duration_ms,
            "events": [e.as_dict() if hasattr(e, "as_dict") else e for e in self.events],
            "node_count": self.node_count,
            "capability_call_count": self.capability_call_count,
            "module_call_count": self.module_call_count,
            "llm_call_count": self.llm_call_count,
            "memory_write_count": self.memory_write_count,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "redaction_applied": self.redaction_applied,
        }

    def timeline_summary(self) -> dict:
        return {
            "total_duration_ms": self.total_duration_ms,
            "node_count": self.node_count,
            "capability_call_count": self.capability_call_count,
            "module_call_count": self.module_call_count,
            "llm_call_count": self.llm_call_count,
            "memory_write_count": self.memory_write_count,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
        }
