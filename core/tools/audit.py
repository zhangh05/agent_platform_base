# core/tools/audit.py
"""Tool Runtime audit metadata builder — lightweight structured event records.

Records minimal, safe metadata about each tool invocation.
Does NOT record: full arguments, full output, keys, passwords, tokens, paths.
"""

from datetime import datetime, timezone
from core.tools.schemas import ToolInvocation, ToolResult, ToolSpec


def build_audit_event(
    spec: ToolSpec,
    invocation: ToolInvocation,
    result: ToolResult,
) -> dict:
    """Build a safe audit event dict for a tool invocation.

    Contains only metadata summaries — no full config, no secrets, no paths.

    Returns a dict suitable for trace/observability integration or standalone audit.
    """
    event = {
        "event_type": "tool_invocation",
        "invocation_id": invocation.invocation_id,
        "tool_id": invocation.tool_id,
        "tool_category": spec.category,
        "tool_version": spec.version,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "risk_level": spec.risk_level,
        "dry_run": invocation.dry_run,
        "workspace_id": invocation.workspace_id,
        "run_id": invocation.run_id,
        "job_id": invocation.job_id,
        "requested_by": invocation.requested_by[:100],
        "artifact_ids": result.artifact_ids,
        "redacted": result.redacted,
        "warning_count": len(result.warnings),
        "error_count": len(result.errors),
        "output_keys": list(result.output.keys()) if isinstance(result.output, dict) else [],
        "summary": result.summary[:200],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Policy decision summary (safe — no full arguments)
    if result.policy_decision:
        event["policy"] = {
            "allowed": result.policy_decision.allowed,
            "risk_level": result.policy_decision.risk_level,
            "blocked_rules": result.policy_decision.blocked_rules,
        }

    return event
