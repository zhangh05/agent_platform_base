# core/tools/context.py
"""ToolRuntimeContext — carries invocation context from caller through to ToolInvocation.

Provides a standard way for Module / Service layers to pass workspace, run, job,
caller identity, and already-validated approval information when invoking tools.

Example usage in a Module service:
    ctx = ToolRuntimeContext(
        workspace_id=validated_workspace_id,
        run_id=run_id,
        module="example_module",
        skill="example_skill",
        requested_by="turn_runner",
        approval_id=approved_id,  # only after the caller has validated it
    )
    client = get_default_tool_runtime_client()
    result = client.invoke("workspace.metadata.get", {}, context=ctx)
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Callable, Optional


_runtime_cancel_check: ContextVar[Optional[Callable[[], bool]]] = ContextVar(
    "lzcore_runtime_cancel_check", default=None
)
_runtime_operation_context: ContextVar[Optional[tuple[str, str, str]]] = ContextVar(
    "lzcore_runtime_operation_context", default=None
)


def bind_runtime_cancel_check(
    cancel_check: Optional[Callable[[], bool]],
) -> Token:
    """Bind a server-owned cancellation callback for one tool task.

    The ContextVar is process-local and is deliberately never serialised.
    ``asyncio.create_task`` and ``asyncio.to_thread`` preserve this binding
    for the handler invocation without exposing it to model arguments.
    """
    return _runtime_cancel_check.set(cancel_check)


def reset_runtime_cancel_check(token: Token) -> None:
    _runtime_cancel_check.reset(token)


def get_runtime_cancel_check() -> Optional[Callable[[], bool]]:
    return _runtime_cancel_check.get()


def bind_runtime_operation_context(
    workspace_id: str, operation_id: str, call_id: str,
) -> Token:
    """Bind server-created operation correlation for one side-effecting call."""
    return _runtime_operation_context.set((workspace_id, operation_id, call_id))


def reset_runtime_operation_context(token: Token) -> None:
    _runtime_operation_context.reset(token)


def get_runtime_operation_context() -> Optional[tuple[str, str, str]]:
    """Return trusted operation correlation; never sourced from model arguments."""
    return _runtime_operation_context.get()


@dataclass
class ToolRuntimeContext:
    """Standard context carried through tool invocations.

    All fields are optional — tools must work with partial context.
    None values mean "not set by caller" and are preserved as-is.
    """

    workspace_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    task_id: Optional[str] = None
    trace_id: Optional[str] = None
    job_id: Optional[str] = None
    capability: Optional[str] = None
    skill: Optional[str] = None
    skill_connection_ids: tuple[str, ...] = ()
    module: Optional[str] = None
    requested_by: str = ""
    dry_run_default: bool = False
    approval_id: Optional[str] = None
    # Server-owned parent run used only to validate a resolved approval during
    # an approval continuation. Normal invocations leave this unset and use
    # run_id as their approval binding.
    approval_run_id: Optional[str] = None
    # Server-owned process-local callback. Never serialise or accept this
    # from model arguments, transport metadata, or durable records.
    cancel_check: Optional[Callable[[], bool]] = None

    def as_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "job_id": self.job_id,
            "capability": self.capability,
            "skill": self.skill,
            "skill_connection_ids": list(self.skill_connection_ids),
            "module": self.module,
            "requested_by": self.requested_by,
            "dry_run_default": self.dry_run_default,
            "approval_id": self.approval_id,
            "approval_run_id": self.approval_run_id,
        }
