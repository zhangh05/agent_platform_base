# core/tools/client.py
"""ToolRuntimeClient — standard, controlled, auditable interface for invoking tools.

Designed for Module / Service layers (NOT direct Agent invocation).
All invocations go through ToolPolicy, ToolExecutor, redaction, and audit.

Example:
    client = get_default_tool_runtime_client()
    ctx = ToolRuntimeContext(
        workspace_id=validated_workspace_id,
        module="example_module",
        requested_by="turn_runner",
    )
    result = client.invoke("workspace.metadata.get", {}, context=ctx)
"""

import logging
from typing import Optional

from core.tools.schemas import ToolSpec, ToolInvocation, ToolResult
from core.tools.registry import ToolRegistry
from core.tools.policy import ToolPolicy
from core.tools.executor import ToolExecutor, canonicalize_tool_arguments
from core.tools.context import ToolRuntimeContext

_LOG = logging.getLogger(__name__)


class ToolRuntimeClient:
    """Standard client for invoking tools through the full safety pipeline.

    Does NOT bypass ToolPolicy. Does NOT write Memory. Does NOT call LLM.
    Uses independent ToolInvocation / ToolResult (not agent/state.py fields).
    """

    def __init__(self, registry: ToolRegistry, policy: ToolPolicy = None):
        self._registry = registry
        self._policy = policy or ToolPolicy()
        self._executor = ToolExecutor(self._registry, self._policy)

    def canonicalize_arguments(self, tool_id: str, arguments: dict = None) -> dict:
        """Project arguments using the exact ToolSpec used by this client.

        Validation and execution use the same schema defaults inserted by
        ToolExecutor.
        """
        try:
            from core.tools.tool_namespace import get_canonical_tool_id
            tool_id = get_canonical_tool_id(tool_id)
        except (KeyError, TypeError, ValueError):
            _LOG.debug("tool id normalization failed for %r", tool_id, exc_info=True)
        spec = self._registry.get_tool(tool_id)
        return canonicalize_tool_arguments(arguments, spec.input_schema if spec else {})

    def invoke(
        self,
        tool_id: str,
        arguments: dict = None,
        *,
        dry_run: bool = None,
        context: ToolRuntimeContext = None,
    ) -> ToolResult:
        """Invoke a tool through the full safety pipeline.

        Args:
            tool_id: The tool to invoke (e.g. "workspace.metadata.get").
            arguments: Tool arguments dict.
            dry_run: Override dry_run. If None, uses context.dry_run_default.
            context: Optional ToolRuntimeContext for workspace/run/job/caller info.

        Returns:
            ToolResult with status, redacted output, audit metadata.
            Never raises — all errors are captured in ToolResult.
        """
        try:
            from core.tools.tool_namespace import resolve_tool_id
            tool_id = resolve_tool_id(tool_id)
        except Exception:
            _LOG.debug("tool id normalization failed for %r", tool_id, exc_info=True)
        arguments = arguments or {}

        # ── Resolve dry_run ──
        if dry_run is None and context is not None:
            dry_run = getattr(context, "dry_run_default", False)
        dry_run = dry_run or False

        # ── Build invocation ──
        invocation = ToolInvocation(
            tool_id=tool_id,
            arguments=arguments,
            workspace_id=getattr(context, "workspace_id", None) if context else None,
            session_id=getattr(context, "session_id", None) if context else None,
            run_id=getattr(context, "run_id", None) if context else None,
            task_id=getattr(context, "task_id", None) if context else None,
            job_id=getattr(context, "job_id", None) if context else None,
            dry_run=dry_run,
            requested_by=getattr(context, "requested_by", "") if context else "",
            skill=getattr(context, "skill", None) if context else None,
            skill_connection_ids=tuple(getattr(context, "skill_connection_ids", ()) or ()) if context else (),
            cancel_check=getattr(context, "cancel_check", None) if context else None,
        )

        # ── v3.10 Phase 6: Single manifest lookup (caller + existence) ──
        try:
            from core.tools.manifest_registry import get_manifest
        except Exception:
            get_manifest = None

        manifest = get_manifest(tool_id) if get_manifest else None

        if not manifest:
            return self._record_result(ToolResult(
                tool_id=tool_id, status="blocked",
                summary=f"Tool {tool_id} has no manifest — execution denied",
                redacted=True,
            ))

        caller = invocation.requested_by or ""
        if not caller:
            return self._record_result(ToolResult(
                tool_id=tool_id, status="blocked",
                summary=(
                    "Caller identity (requested_by) is required. "
                    "Set ToolRuntimeContext.requested_by to one of: "
                    + ", ".join(manifest.allowed_callers)
                ),
                errors=["caller_missing"],
                redacted=True,
            ))
        if caller not in manifest.allowed_callers:
            return self._record_result(ToolResult(
                tool_id=tool_id, status="blocked",
                summary=f"Caller '{caller}' not allowed for {tool_id}",
                redacted=True,
            ))

        # ── Execute through full pipeline ──
        result = self._executor.execute(invocation)
        self._append_trace_event(result, context)
        return self._record_result(result)

    @staticmethod
    def _record_result(result: ToolResult) -> ToolResult:
        try:
            from observability.metrics import record_operation
            status = "succeeded" if result.status in ("succeeded", "dry_run") else str(result.status or "failed")
            record_operation("tool", status)
        except (ImportError, TypeError, ValueError):
            _LOG.debug("tool metric update failed", exc_info=True)
        return result

    def list_tools(self) -> list:
        """List all registered tools as metadata dicts (no handler callables)."""
        return self._registry.list_tools()

    def get_tool(self, tool_id: str) -> Optional[dict]:
        """Get tool metadata by id. Returns dict (no handler)."""
        spec = self._registry.get_tool(tool_id)
        return spec.as_dict() if spec else None

    @property
    def tool_count(self) -> int:
        return self._registry.count()

    def _append_trace_event(self, result: ToolResult, context: ToolRuntimeContext = None):
        """Append safe ToolResult metadata to an existing observability trace."""
        if not context or not context.trace_id:
            return
        try:
            from observability.store import append_event
            from core.tools.trace_metadata import build_trace_metadata_from_tool_result

            meta = build_trace_metadata_from_tool_result(result)
            meta.update({
                "workspace_id": getattr(context, "workspace_id", "") or "",
                "session_id": getattr(context, "session_id", "") or "",
                "run_id": getattr(context, "run_id", "") or "",
                "task_id": getattr(context, "task_id", "") or "",
                "job_id": getattr(context, "job_id", "") or "",
                "capability": getattr(context, "capability", "") or "",
                "skill": getattr(context, "skill", "") or "",
                "module": getattr(context, "module", "") or "",
            })
            status = "success" if result.status in ("succeeded", "dry_run") else "failed"
            append_event(getattr(context, "trace_id", ""), {
                "trace_id": getattr(context, "trace_id", ""),
                "run_id": getattr(context, "run_id", ""),
                "workspace_id": getattr(context, "workspace_id", "") or "",
                "event_type": "tool_runtime",
                "name": f"tool:{result.tool_id}",
                "status": status,
                "duration_ms": result.duration_ms,
                "summary": f"tool:{result.tool_id}: {result.status}",
                "metadata": meta,
                "redaction_applied": True,
            }, ws_id=context.workspace_id or "")
        except Exception:
            _LOG.debug("tool trace append failed", exc_info=True)
