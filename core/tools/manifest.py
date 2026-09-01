# core/tools/manifest.py
"""Capability Manifest 2.0 — single source of truth for tool capabilities."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal

ActionClass = Literal["read","write","execute","network","delete","admin"]
RiskLevel = Literal["low","medium","high","critical"]
SideEffect = Literal["none","read","write","delete","remote_exec","network_change"]
Idempotency = Literal["safe_to_retry","unsafe_to_retry","unknown"]
RollbackStrategy = Literal["none","soft_delete_restore","artifact_restore","custom"]
OutputSensitivity = Literal["public","internal","sensitive","secret"]
CallerType = Literal["turn_runner","rest_api","job_runner","subagent"]

# Default caller set for all tools. Individual manifests override this
# only when a tool must be restricted to a subset of callers.
DEFAULT_ALLOWED_CALLERS: list[CallerType] = [
    "turn_runner", "rest_api", "job_runner", "subagent",
]

@dataclass
class RetryPolicy:
    max_attempts: int = 1
    backoff: str = "none"  # none | linear | exponential
    retry_on: list[str] = field(default_factory=list)  # error types

@dataclass
class CapabilityManifest:
    tool_id: str
    category: str = "general"
    display_name: str = ""
    description: str = ""

    # ── Risk & safety ──
    action_class: ActionClass = "read"
    risk_level: RiskLevel = "low"
    destructive: bool = False
    side_effects: SideEffect = "none"

    # ── Retry & rollback ──
    idempotency: Idempotency = "unknown"
    rollback_strategy: RollbackStrategy = "none"
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    # ── I/O sensitivity ──
    secret_fields: list[str] = field(default_factory=list)
    output_sensitivity: OutputSensitivity = "internal"

    # ── Access control ──
    allowed_callers: list[CallerType] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_CALLERS)
    )
    workspace_scope_required: bool = True

    # ── Artifact ──
    reads_artifact: bool = False
    writes_artifact: bool = False

    # ── Limits ──
    timeout_seconds: int = 30
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)

    # ── Display ──

    def validate(self) -> list[str]:
        """Return list of validation errors. Empty = valid."""
        errors = []
        if not self.tool_id: errors.append("tool_id required")
        if not self.display_name: errors.append("display_name required")
        if not self.category: errors.append("category required")
        if self.timeout_seconds < 1: errors.append("timeout_seconds must be >= 1")
        return errors
