"""Subagent orchestration tools."""

from __future__ import annotations

from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id

from core.tools.general_tools.shared import _caller_workspace, _error_inv, _ok, _result

# Re-export the BUILTIN_PROFILES from subagent runtime for validation
from agent.runtime.durable.subagent import BUILTIN_PROFILES, SubagentProfile


_TERMINAL_SUBTASK_STATUSES = {"succeeded", "failed", "cancelled", "canceled"}


def _subtask_tracking(subtask_id: str, status: str) -> dict:
    normalized_status = str(status or "running").strip().lower()
    done = normalized_status in _TERMINAL_SUBTASK_STATUSES
    return {
        "kind": "long_task",
        "domain": "subagent",
        "task_id": subtask_id,
        "status": normalized_status,
        "done": done,
        "terminal": done,
        "next_poll_seconds": 2,
        "suggested_next_action": "synthesize_results" if done else "poll_get",
        "poll_action": "get",
        "poll_arguments": {"action": "get", "subtask_id": subtask_id},
    }


# ── Subagent execution ───────────────────────────────────────────────


def _get_profile(profile_id: str) -> SubagentProfile | None:
    return BUILTIN_PROFILES.get(profile_id)


def _inv_session_id(inv: ToolInvocation) -> str:
    args = inv.arguments or {}
    return str(args.get("session_id") or getattr(inv, "session_id", "") or "").strip()


def _run_durable_subagent(*, instruction: str, workspace_id: str, session_id: str,
                          parent_task_id: str = "",
                          profile_id: str = "research_agent",
                          max_turns: int | None = None,
                          background: bool = False) -> dict:
    from agent.runtime.durable.subagent import (
        create_subagent_task,
        start_subagent_task,
        merge_subagent_result,
        run_subagent_task,
    )
    from core.tools.context import get_runtime_operation_context
    runtime_operation = get_runtime_operation_context()

    profile = _get_profile(profile_id)
    if not profile:
        return {
            "ok": False,
            "status": "failed",
            "error_code": "ARG_ENUM_INVALID",
            "error": f"unknown profile_id: {profile_id}",
            "error_details": {
                "field": "profile_id",
                "invalid_value": profile_id,
                "allowed_values": list(BUILTIN_PROFILES),
            },
            "retryable": False,
        }

    effective_turns = min(max_turns or profile.max_steps, profile.max_steps)

    created = create_subagent_task(
        parent_task_id=parent_task_id,
        workspace_id=workspace_id,
        session_id=session_id,
        profile_id=profile_id,
        goal=instruction,
        context_refs=[],
        max_steps=effective_turns,
        operation_id=(runtime_operation[1] if runtime_operation and runtime_operation[0] == workspace_id else ""),
        operation_call_id=(runtime_operation[2] if runtime_operation and runtime_operation[0] == workspace_id else ""),
    )
    if not created.get("ok"):
        return {"ok": False, "error": created.get("error", "failed to create subagent task")}

    subtask_id = created["subtask_id"]

    if runtime_operation and runtime_operation[0] == workspace_id:
        from core.runtime_engine.operation_ledger import link_operation_resource
        link_operation_resource(
            workspace_id,
            runtime_operation[1],
            resource_kind="subagent",
            resource_id=subtask_id,
        )

    if background:
        started = start_subagent_task(subtask_id, workspace_id)
        if not started.get("ok"):
            return started
        status = str(started.get("status") or "running")
        return {
            "ok": True, "subtask_id": subtask_id,
            "status": status,
            "background": True,
            "tracking": _subtask_tracking(subtask_id, status),
            "summary": f"Subagent {profile.name} started in background (task: {subtask_id})",
            "_hint": f"Subagent {profile_id} launched in background (task: {subtask_id})",
        }

    result = run_subagent_task(subtask_id, workspace_id)
    if result.get("ok") and result.get("status") == "succeeded":
        merge_subagent_result(parent_task_id, subtask_id, workspace_id)
    return {
        "ok": result.get("ok", False) and result.get("status") == "succeeded",
        "final_response": result.get("summary", ""),
        "summary": result.get("summary", ""),
        "subtask_id": subtask_id,
        "profile_id": profile_id,
        "agent_name": profile.name,
        "status": result.get("status", "unknown"),
        "findings": result.get("findings", []),
        "tool_results": result.get("tool_results", []),
        "errors": result.get("errors", []),
        "warnings": result.get("warnings", []),
    }


# ── Generic spawn dispatcher ─────────────────────────────────────────


def _spawn_agent(inv: ToolInvocation, profile_id: str) -> dict:
    """Generic dispatcher for spawning a subagent of a specific profile."""
    args = inv.arguments
    instruction = str(args.get("instruction", "")).strip()
    try:
        max_turns = int(args.get("max_turns", 0) or 0)
    except (TypeError, ValueError):
        return _error_inv(inv, "max_turns must be an integer")
    background = args.get("background") is not False

    if not instruction:
        return _error_inv(inv, "instruction is required")

    profile = _get_profile(profile_id)
    if not profile:
        return _error_inv(
            inv,
            f"unknown profile_id: {profile_id}",
            error_code="ARG_ENUM_INVALID",
            details={
                "field": "profile_id",
                "invalid_value": profile_id,
                "allowed_values": list(BUILTIN_PROFILES),
            },
        )

    workspace_id = _caller_workspace(inv)
    # Omission means "use the selected profile's budget". A hidden generic
    # default previously reduced every profile to five turns and made valid
    # delegated research fail before synthesis.
    effective_turns = max_turns or profile.max_steps

    try:
        validate_workspace_id(workspace_id)
        result = _run_durable_subagent(
            instruction=instruction,
            workspace_id=workspace_id,
            session_id=_inv_session_id(inv),
            parent_task_id=getattr(inv, "task_id", "") or "",
            profile_id=profile_id,
            max_turns=effective_turns,
            background=background,
        )
        return _result(inv, result.get("ok", False), {
            **result,
            "_hint": (
                f"Subagent {profile_id} "
                + ("已启动（后台）。" if background else f"完成，状态: {result.get('status')}。")
                + f" subtask_id: {result.get('subtask_id')}。"
                + " 用 agent.manage(action=get) 获取详细结果。"
            ),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


# ── Other action handlers ────────────────────────────────────────────


def handle_agent_spawn(inv: ToolInvocation) -> dict:
    """Spawn a durable subagent using a generic base profile."""
    args = inv.arguments or {}
    profile_id = str(args.get("profile_id") or "research_agent").strip()
    return _spawn_agent(inv, profile_id=profile_id)


def handle_agent_list(inv: ToolInvocation) -> dict:
    """List available agent profiles with capabilities."""
    profiles = []
    for pid, p in BUILTIN_PROFILES.items():
        profiles.append({
            "profile_id": pid,
            "name": p.name,
            "description": p.description,
            "max_steps": p.max_steps,
            "allowed_tools": p.allowed_tools,
            "can_modify_files": p.can_modify_files,
            "can_execute_commands": p.can_execute_commands,
            "can_call_network": p.can_call_network,
        })
    return _ok(inv, "", {
        "profiles": profiles,
        "count": len(profiles),
        "_hint": "可用子Agent profile: " + ", ".join(BUILTIN_PROFILES.keys()),
    })


def handle_agent_get_result(inv: ToolInvocation) -> dict:
    """Get a subagent result by its canonical subtask/session identifier."""
    args = inv.arguments or {}
    ws = _caller_workspace(inv)
    subtask_id = str(args.get("subtask_id") or "").strip()

    if not subtask_id:
        return _error_inv(inv, "subtask_id is required")

    try:
        validate_workspace_id(ws)
        from agent.runtime.durable.subagent import get_subagent_task
        persisted = get_subagent_task(ws, subtask_id)
        if persisted is not None:
            status = str(persisted.get("status") or "unknown")
            payload = {
                "workspace_id": ws,
                **persisted,
                "tracking": _subtask_tracking(subtask_id, status),
            }
            payload.setdefault("subtask_id", subtask_id)
            payload.setdefault("preview", str(persisted.get("summary") or ""))
            payload.setdefault("artifact_id", "")
            artifact_id = str(persisted.get("result_artifact_id") or "").strip()
            if status == "succeeded" and artifact_id:
                # This remains inside the registered agent.manage handler. The
                # artifact is the durable authority for a completed child result;
                # do not downgrade it to the diagnostic summary field.
                from artifacts.store import read_artifact_content
                full_result = read_artifact_content(ws, artifact_id)
                if full_result is not None:
                    payload.update({
                        "artifact_id": artifact_id,
                        "artifact_ids": [artifact_id],
                        "artifact_type": "output_data",
                        "preview": full_result,
                        "content_chars": len(full_result),
                        "content_complete": True,
                        "subagent_result_complete": True,
                    })
                    return _ok(
                        inv,
                        f"Subagent result ready: {len(full_result)} chars in artifact {artifact_id}",
                        payload,
                    )
            if status in {"failed", "cancelled", "canceled"}:
                error_code = (
                    "SUBAGENT_CANCELLED"
                    if status in {"cancelled", "canceled"}
                    else "SUBAGENT_FAILED"
                )
                errors = [str(item) for item in (persisted.get("errors") or []) if str(item)]
                summary = str(
                    persisted.get("summary")
                    or (errors[0] if errors else f"Subagent {status}")
                )
                return _result(inv, False, {
                    **payload,
                    "summary": summary,
                    "error": summary,
                    "errors": errors or [summary],
                    "error_code": error_code,
                    "retryable": False,
                })
            return _ok(inv, str(persisted.get("summary") or f"Subagent status: {status}"), payload)

        return _error_inv(inv, "subtask not found")
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_agent_cancel(inv: ToolInvocation) -> dict:
    """Cancel a running subagent by subtask_id."""
    args = inv.arguments
    subtask_id = str(args.get("subtask_id", "")).strip()
    if not subtask_id:
        return _error_inv(inv, "subtask_id is required")
    try:
        ws = _caller_workspace(inv)
        validate_workspace_id(ws)
        from agent.runtime.durable.subagent import cancel_subagent_task
        cancelled = cancel_subagent_task(subtask_id, ws)
        if not cancelled.get("ok"):
            return _error_inv(inv, cancelled.get("error", "cancel failed"))
        return _ok(inv, "", {
            "subtask_id": subtask_id, "cancelled": True,
            "_hint": f"Subagent {subtask_id} 已取消。",
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_agent_merge(inv: ToolInvocation) -> dict:
    """Merge a completed subagent result into the parent task record."""
    args = inv.arguments or {}
    subtask_id = str(args.get("subtask_id") or "").strip()
    parent_task_id = str(args.get("parent_task_id") or getattr(inv, "task_id", "") or "").strip()
    if not subtask_id:
        return _error_inv(inv, "subtask_id is required")
    if not parent_task_id:
        return _error_inv(inv, "parent_task_id is required")
    try:
        ws = _caller_workspace(inv)
        validate_workspace_id(ws)
        from agent.runtime.durable.subagent import merge_subagent_result
        return merge_subagent_result(parent_task_id, subtask_id, ws)
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_agent_status(inv: ToolInvocation) -> dict:
    """List all running/completed subagent tasks."""
    try:
        ws = _caller_workspace(inv)
        validate_workspace_id(ws)
        from agent.runtime.durable.subagent import list_subagent_tasks
        tasks = list_subagent_tasks(ws)
        return _ok(inv, "", {
            "tasks": tasks, "count": len(tasks),
            "_hint": f"{len(tasks)} 个子Agent任务。用 agent.manage(action=cancel) 取消运行中的任务。",
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    # Other action handlers
    'handle_agent_spawn',
    'handle_agent_list',
    'handle_agent_get_result',
    'handle_agent_cancel',
    'handle_agent_merge',
    'handle_agent_status',
]
