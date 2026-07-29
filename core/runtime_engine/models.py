"""
Core data models for SSOT Runtime Engine.

These define the QueryLoop call, result, budget, and audit types.
No business logic — pure dataclasses and enums.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any


class ExecutionStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class NodePriority(enum.Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class RiskLevel(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ExecutionNode:
    """A normalized QueryLoop tool call ready for policy and execution."""
    id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    status: ExecutionStatus = ExecutionStatus.PENDING
    result: Any | None = None
    error: str | None = None
    retry_count: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    latency_ms: float = 0.0
    priority: NodePriority = NodePriority.NORMAL
    optional: bool = False
    node_run_id: str = ""
    approval_required: bool = False
    approval_granted: bool = False
    # Action-alias normalization bookkeeping for audit and diagnostics.
    action_original: str = ""
    action_normalized_from_alias: bool = False

    @property
    def is_ready(self) -> bool:
        return self.status == ExecutionStatus.PENDING

    @property
    def is_done(self) -> bool:
        return self.status in (
            ExecutionStatus.SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.SKIPPED,
        )

@dataclass
class StatelessContext:
    """Minimal stateless context — a flat snapshot.

    No pipeline, no multi-stage rebuild, no precomputed evidence.
    Lazy injection per-node on demand.
    """
    workspace_id: str
    session_id: str
    request_id: str
    user_input: str
    cwd: str = ""
    os: str = ""
    timestamp: float = field(default_factory=time.time)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Standardized result from any tool execution."""
    node_id: str
    tool: str
    success: bool
    data: Any = None
    error: str | None = None
    error_code: str = ""
    latency_ms: float = 0.0
    retry_count: int = 0
    # v3.10 (tool retry): per-node retry provenance. Written by
    # the execution engine when a retry actually fired, and
    # surfaced to audit / trace / SSOTRuntimeResult metadata.
    metadata: dict[str, Any] = field(default_factory=dict)
    # v4.1 (diagnostic preservation): raw error code from the tool
    # handler and the normalized code after the resolver. These
    # are always present so audit/retry/response projection never lose error
    # provenance.
    error_code_raw: str = ""
    error_code_norm: str = ""


@dataclass
class SSOTRuntimeConfig:
    """Configuration for the SSOT Runtime Engine."""
    max_retries_per_node: int = 1
    parallel_layer_timeout_ms: int = 300_000
    single_node_timeout_ms: int = 120_000
    planner_timeout_ms: int = 20_000
    max_query_loop_iterations: int = 20
    max_nodes: int = 30
    max_depth: int = 8
    max_global_concurrency: int = 8
    max_layer_concurrency: int = 5
    max_total_seconds: int = 60
    max_tool_seconds: int = 30
    max_llm_calls: int = 50
    tracking_enabled: bool = True
    tracking_max_polls: int = 8
    tracking_max_seconds: int = 45
    tracking_poll_interval_cap_seconds: float = 2.0

    # One input-budget contract for the active runtime. Tool definitions remain
    # fully visible and are deducted before message/history/tool-result budgets.
    context_window_tokens: int = 0
    max_input_tokens: int = 48_000
    max_output_tokens: int = 4096
    context_safety_tokens: int = 2048

    # RiskPolicy warning thresholds. These no longer trigger approval/blocking;
    # QueryLoop budgets enforce hard runtime limits.
    rp_max_tool_nodes_allow: int = 20
    rp_max_tool_nodes_approval: int = 50

    # Exec.run command count warning threshold.
    rp_max_exec_allow: int = 5
    rp_max_exec_approval: int = 20


@dataclass
class SSOTRuntimeResult:
    """Final result from a SSOT Runtime Engine execution."""
    request_id: str
    success: bool
    total_latency_ms: float = 0.0
    planner_latency_ms: float = 0.0
    execution_latency_ms: float = 0.0
    merge_latency_ms: float = 0.0
    response_latency_ms: float = 0.0
    max_layer_latency_ms: float = 0.0
    node_results: dict[str, ToolResult] = field(default_factory=dict)
    final_response: str = ""
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def node_success_count(self) -> int:
        return sum(1 for r in self.node_results.values() if r.success)

    @property
    def node_failure_count(self) -> int:
        return sum(1 for r in self.node_results.values() if not r.success)


# ============================================================================
# Bank-grade additions
# ============================================================================

@dataclass
class ExecutionBudget:
    """Per-request execution budget — enforced by BudgetController."""
    max_total_seconds: int = 60
    max_planner_seconds: int = 20
    max_tool_seconds: int = 30
    max_nodes: int = 30
    max_depth: int = 8
    max_parallel_width: int = 8
    max_llm_calls: int = 50


@dataclass
class AuditRecord:
    """Immutable audit record for a single request."""
    request_id: str
    session_id: str
    created_at: float = field(default_factory=time.time)
    user_request_hash: str = ""
    planner_model: str = ""
    llm_call_count: int = 0
    tool_call_count: int = 0
    risk_level: str = "low"
    approval_required: bool = False
    executed_nodes: list[dict[str, Any]] = field(default_factory=list)
    blocked_nodes: list[dict[str, Any]] = field(default_factory=list)
    failed_nodes: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class TraceSpan:
    """A single span in the execution trace."""
    name: str
    start_time: float
    end_time: float = 0.0
    duration_ms: float = 0.0
    status: str = "pending"
    error_code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list["TraceSpan"] = field(default_factory=list)


@dataclass
class MetricSnapshot:
    """Structured metrics for every run."""
    total_duration_ms: float = 0.0
    planner_duration_ms: float = 0.0
    validation_duration_ms: float = 0.0
    execution_duration_ms: float = 0.0
    response_duration_ms: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    tool_success: int = 0
    tool_failed: int = 0
    cache_hit_ratio: float = 0.0
    max_parallel_width: int = 0
    risk_level: str = "low"
    context_compacted: bool = False
    context_estimated_chars: int = 0
    context_estimated_tokens: int = 0
    context_budget_tokens: int = 0
    context_saved_chars: int = 0
    compact_detail: dict = field(default_factory=dict)
