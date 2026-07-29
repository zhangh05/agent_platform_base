"""Safe Tool Runtime trace metadata helpers.

v3.0: trace metadata is canonical-only. The trace carries the
canonical tool_id, the status, and the policy decision. There is
no per-trace aliasing field because v3.0 has no alias layer; the
canonical id IS the dispatch key.
"""

from core.tools.schemas import ToolResult


def build_trace_metadata_from_tool_result(result: ToolResult) -> dict:
    """Build allowlisted metadata for trace/observability stores."""
    policy = result.policy_decision
    return {
        "invocation_id": result.invocation_id,
        "tool_id": result.tool_id,
        "canonical_tool_id": result.tool_id,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "dry_run": result.status == "dry_run",
        "redacted": result.redacted,
        "policy_allowed": policy.allowed if policy else None,
        "policy_reason": (policy.reason[:200] if policy and policy.reason else ""),
        "risk_level": policy.risk_level if policy else "",
        "artifact_ids": list(result.artifact_ids or [])[:50],
    }
