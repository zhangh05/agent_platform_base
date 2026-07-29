"""
Execution Trace System for SSOT Runtime Engine.

Generates request and QueryLoop call spans.

Each span has: start_time, end_time, duration_ms, status, error_code, metadata.
"""

from __future__ import annotations

import time
from typing import Any

from .models import (
    ExecutionNode,
    TraceSpan,
    ToolResult,
)


class SpanClock:
    """Context-managed timing for a single span."""

    def __init__(self, name: str, **metadata):
        self._span = TraceSpan(
            name=name,
            start_time=time.monotonic(),
            metadata=metadata,
        )

    def start(self):
        self._span.start_time = time.monotonic()
        return self

    def stop(self, status: str = "ok", error_code: str = ""):
        self._span.end_time = time.monotonic()
        self._span.duration_ms = (self._span.end_time - self._span.start_time) * 1000
        self._span.status = status
        self._span.error_code = error_code

    @property
    def span(self) -> TraceSpan:
        return self._span


class TraceCollector:
    """Collects and organizes execution traces."""

    def __init__(self):
        self._root: TraceSpan | None = None
        self._current: list[TraceSpan] = []

    def start_request(self, request_id: str) -> SpanClock:
        clock = SpanClock("request", request_id=request_id)
        self._root = clock.span
        return clock

    def add_span(self, name: str, **metadata) -> SpanClock:
        return SpanClock(name, **metadata)

    def add_child(self, parent: SpanClock, child: SpanClock) -> None:
        parent.span.children.append(child.span)

    def add_node_span(self, node: ExecutionNode) -> SpanClock:
        # v3.10: surface action-alias provenance on per-node spans so
        # any later trace dump reveals planner terminology drift.
        metadata = {
            "node_id": node.id,
            "tool": node.tool,
        }
        if node.action_normalized_from_alias:
            metadata["action_original"] = node.action_original
            metadata["action_normalized"] = node.args.get("action", "")
            metadata["normalized_from_alias"] = True
        return SpanClock(
            f"tool_call.{node.id}",
            **metadata,
        )

    def finalize(
        self,
        request_span: SpanClock | None,
        node_results: dict[str, ToolResult],
        risk_level: str = "low",
    ) -> TraceSpan | None:
        """Stop the request span and return the root trace."""
        if request_span:
            request_span.stop()
            return request_span.span
        return self._root

    def to_dict(self, span: TraceSpan) -> dict[str, Any]:
        """Convert trace tree to dict."""
        return {
            "name": span.name,
            "duration_ms": span.duration_ms,
            "status": span.status,
            "children": [self.to_dict(c) for c in span.children],
        } if span else {}
