"""
Metrics System for SSOT Runtime Engine.

Every run must emit structured metrics:
  - Timing breakdown (reasoning, validation, execution, response)
  - LLM call count (bounded by the QueryLoop runtime budget)
  - Tool call count (success/failure)
  - QueryLoop execution concurrency
  - Risk level

Used for observability and latency benchmarking.
"""

from __future__ import annotations

import time
from typing import Any

from .models import (
    MetricSnapshot,
    ToolResult,
)


class MetricsCollector:
    """Collects and emits structured metrics per run."""

    def __init__(self):
        self._snapshot = MetricSnapshot()

    def capture_planner(self, duration_ms: float) -> None:
        self._snapshot.planner_duration_ms = duration_ms

    def capture_validation(self, duration_ms: float) -> None:
        self._snapshot.validation_duration_ms = duration_ms

    def capture_query_loop_execution(
        self,
        duration_ms: float,
        node_results: dict[str, ToolResult],
        max_parallel_width: int,
    ) -> None:
        """Capture execution facts for the iterative QueryLoop path."""
        self._snapshot.execution_duration_ms = max(0.0, float(duration_ms or 0.0))
        self._snapshot.tool_calls = len(node_results)
        self._snapshot.tool_success = sum(1 for r in node_results.values() if r.success)
        self._snapshot.tool_failed = sum(1 for r in node_results.values() if not r.success)
        self._snapshot.max_parallel_width = max(0, int(max_parallel_width or 0))

    def capture_response(self, duration_ms: float) -> None:
        self._snapshot.response_duration_ms = duration_ms

    def capture_total(self, duration_ms: float) -> None:
        self._snapshot.total_duration_ms = duration_ms

    def set_llm_calls(self, count: int) -> None:
        self._snapshot.llm_calls = count

    def set_risk_level(self, level: str) -> None:
        self._snapshot.risk_level = level

    def capture_context_usage(
        self,
        estimated_chars: int,
        *,
        estimated_tokens: int = 0,
        budget_tokens: int = 0,
    ) -> None:
        """Record context usage against the active runtime token budget."""
        prev = self._snapshot.context_estimated_tokens
        self._snapshot.context_estimated_chars = estimated_chars
        self._snapshot.context_estimated_tokens = estimated_tokens
        self._snapshot.context_budget_tokens = budget_tokens
        if estimated_tokens > 0 and budget_tokens > 0 and prev > 0:
            growth = estimated_tokens - prev
            if growth > 0:
                remaining = max(0, budget_tokens - estimated_tokens)
                self._snapshot.compact_detail["iterations_until_compact"] = (
                    remaining // growth if growth > 0 else 999
                )
                self._snapshot.compact_detail["growth_tokens_per_iteration"] = growth

    def mark_compacted(self, info) -> None:
        """Record a compaction event with full detail."""
        self._snapshot.context_compacted = True
        self._snapshot.context_saved_chars = info.saved_chars
        self._snapshot.compact_detail.update({
            "trigger": "threshold_exceeded",
            "turns_removed": info.removed,
            "tools_affected": info.tools_used,
            "before_chars": info.before_chars,
            "after_chars": info.after_chars,
            "before_tokens": info.before_tokens,
            "after_tokens": info.after_tokens,
            "compression_ratio": (
                f"{info.before_chars / max(info.after_chars, 1):.1f}x"
            ),
            "tool_stats": info.tool_stats,
            "key_hints": info.key_hints,
        })

    def snapshot(self) -> MetricSnapshot:
        return self._snapshot

    def to_dict(self) -> dict[str, Any]:
        s = self._snapshot
        return {
            "total_duration_ms": s.total_duration_ms,
            "planner_duration_ms": s.planner_duration_ms,
            "validation_duration_ms": s.validation_duration_ms,
            "execution_duration_ms": s.execution_duration_ms,
            "response_duration_ms": s.response_duration_ms,
            "llm_calls": s.llm_calls,
            "tool_calls": s.tool_calls,
            "tool_success": s.tool_success,
            "tool_failed": s.tool_failed,
            "cache_hit_ratio": s.cache_hit_ratio,
            "max_parallel_width": s.max_parallel_width,
            "risk_level": s.risk_level,
            "context_compacted": s.context_compacted,
            "context_estimated_chars": s.context_estimated_chars,
            "context_estimated_tokens": s.context_estimated_tokens,
            "context_budget_tokens": s.context_budget_tokens,
            "context_saved_chars": s.context_saved_chars,
            "compact_detail": s.compact_detail,
        }
