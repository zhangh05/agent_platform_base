# agent/state.py
"""AgentState — structured request state for runtime traces and UI records."""

import time
import uuid
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone


def uuid7() -> str:
    """Generate a UUIDv7 (time-ordered) string.

    Format: 8-4-4-4-12 hex digits. First 48 bits encode Unix ms timestamp
    so lexicographic order == chronological order (Codex pattern).
    Falls back to uuid4() on systems where random is unavailable.
    """
    try:
        ts_ms = time.time_ns() // 1_000_000
        # 48-bit timestamp (UUIDv7 spec); mask to force 12 hex chars even
        # when ts_ms fits in fewer bits (current ms precision only uses ~41 bits)
        ts_hex = f"{ts_ms & 0xFFFFFFFFFFFF:012x}"
        # 4-bit version + 12 bits random
        rand_a = uuid.uuid4().int & 0x0FFF
        ver_hex = f"{0x7}{rand_a:03x}"
        # 2-bit variant + 62 bits random
        rand_b = uuid.uuid4().int & ((1 << 62) - 1)
        var_hex = f"{(0b10 << 62) | rand_b:016x}"
        return f"{ts_hex[:8]}-{ts_hex[8:]}-{ver_hex}-{var_hex[:4]}-{var_hex[4:]}"
    except Exception:
        return uuid.uuid4().hex[:12]


@dataclass
class AgentState:
    """Canonical state projection for Agent Platform Base runtime records."""

    request_id: str = field(default_factory=uuid7)
    user_input: str = ""
    intent: Optional[str] = None
    workspace_id: str = ""  # v3.10: must be set explicitly, no default fallback
    session_id: Optional[str] = None

    payload: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    memory_hits: List[Dict[str, Any]] = field(default_factory=list)

    plan: List[str] = field(default_factory=list)

    # ── Capability execution records ──
    # Capability adapter invocation metadata and results.
    capability_calls: List[Dict[str, Any]] = field(default_factory=list)
    skill_results: Dict[str, Any] = field(default_factory=dict)

    # Tool audit projection used by traces, run records, and UI contracts.
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: Dict[str, Any] = field(default_factory=dict)

    verification: Dict[str, Any] = field(default_factory=dict)
    final_response: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None

    # ── UI Actions — Agent → Frontend instruction bridge ──
    # Each entry: {action: str, target: str, value: Optional[str], params: dict}
    ui_actions: List[Dict[str, Any]] = field(default_factory=list)

    runtime_mode: str = "ssot_runtime"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""

    # ── Observability ──
    trace_id: str = ""
    trace_events: List[Dict[str, Any]] = field(default_factory=list)
    node_timings: Dict[str, float] = field(default_factory=dict)

    # ── v3.1.1: Reference context item (Codex pattern) ──
    # The id of the first non-system message kept after the last compaction.
    # Frontend can render this as a "📍 context anchor" marker so users see
    # where LLM's visible context begins.
    reference_context_item_id: str = ""

    def summary(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "intent": self.intent,
            "runtime_mode": self.runtime_mode,
            "verification": self.verification,
            "warnings": self.warnings,
            "reference_context_item_id": self.reference_context_item_id,
        }
