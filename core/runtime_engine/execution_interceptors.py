"""Neutral, extension-owned interception at the tool execution boundary.

The runtime deliberately does not know what an interruption means.  An
extension may ask to suspend one concrete invocation, while the core only
returns a structured result to the QueryLoop.  Approval, change windows, and
other business policies remain extension concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExecutionInterception:
    """A server-created request to defer one tool invocation."""

    extension_id: str
    interruption_id: str
    kind: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)

    def as_tool_output(self) -> dict[str, Any]:
        return {
            "ok": True,
            "executed": False,
            "status": "waiting_external_input",
            "external_interruption": {
                "extension_id": self.extension_id,
                "interruption_id": self.interruption_id,
                "kind": self.kind,
                "summary": self.summary,
                "payload": dict(self.payload),
            },
        }


def before_tool_execution(*, tool_id: str, call_id: str, arguments: dict[str, Any], ctx) -> ExecutionInterception | None:
    """Ask enabled extensions whether a concrete call must be deferred.

    A hook receives server-owned context plus the exact model arguments.  It
    cannot edit those arguments: a changed operation must be represented by a
    new tool call and therefore a new interruption record.
    """
    try:
        from extensions.runtime import execution_interceptors

        hooks = execution_interceptors()
    except Exception:
        return None
    request = {
        "tool_id": str(tool_id),
        "call_id": str(call_id),
        "arguments": dict(arguments or {}),
        "workspace_id": str(getattr(ctx, "workspace_id", "") or ""),
        "session_id": str(getattr(ctx, "session_id", "") or ""),
        "run_id": str(getattr(ctx, "run_id", "") or ""),
        "request_id": str(getattr(ctx, "request_id", "") or ""),
        "workbench_context": dict((getattr(ctx, "extras", {}) or {}).get("workbench_context") or {}),
    }
    for extension_id, hook in hooks:
        try:
            outcome = hook(dict(request))
        except Exception:
            # An optional extension must not turn an unrelated tool call into
            # a synthetic platform failure.  Extension health remains visible
            # through its own lifecycle state.
            continue
        if not isinstance(outcome, dict) or outcome.get("action") != "suspend":
            continue
        interruption_id = str(outcome.get("interruption_id") or "").strip()
        kind = str(outcome.get("kind") or "external_input").strip()
        if not interruption_id or not kind:
            continue
        return ExecutionInterception(
            extension_id=str(outcome.get("extension_id") or extension_id),
            interruption_id=interruption_id,
            kind=kind,
            summary=str(outcome.get("summary") or "等待外部决定"),
            payload=dict(outcome.get("payload") or {}),
        )
    return None
