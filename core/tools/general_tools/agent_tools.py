"""Subagent orchestration tools."""

from __future__ import annotations

from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id

from core.tools.general_tools.shared import _caller_workspace, _error_inv, _ok, _result

# Re-export the BUILTIN_PROFILES from subagent runtime for validation
from agent.runtime.durable.subagent import BUILTIN_PROFILES, SubagentProfile


# ── Subagent execution ───────────────────────────────────────────────


def _get_profile(profile_id: str) -> SubagentProfile | None:
    return BUILTIN_PROFILES.get(profile_id)


def _inv_session_id(inv: ToolInvocation) -> str:
    args = inv.arguments or {}
    return str(args.get("session_id") or getattr(inv, "session_id", "") or "").strip()


def _run_durable_subagent(*, instruction: str, workspace_id: str, session_id: str,
                          parent_task_id: str = "",
                          profile_id: str = "research_agent",
                          max_turns: int = 3,
                          background: bool = False) -> dict:
    from agent.runtime.durable.subagent import (
        create_subagent_task,
        start_subagent_task,
        merge_subagent_result,
        run_subagent_task,
    )

    profile = _get_profile(profile_id)
    if not profile:
        return {"ok": False, "error": f"unknown profile_id: {profile_id}"}

    effective_turns = min(max_turns, profile.max_steps)

    created = create_subagent_task(
        parent_task_id=parent_task_id,
        workspace_id=workspace_id,
        session_id=session_id,
        profile_id=profile_id,
        goal=instruction,
        context_refs=[],
        max_steps=effective_turns,
    )
    if not created.get("ok"):
        return {"ok": False, "error": created.get("error", "failed to create subagent task")}

    subtask_id = created["subtask_id"]

    if background:
        started = start_subagent_task(subtask_id, workspace_id)
        if not started.get("ok"):
            return started
        return {
            "ok": True, "subtask_id": subtask_id,
            "background": True,
            "_hint": f"Subagent {profile_id} launched in background (task: {subtask_id})",
        }

    result = run_subagent_task(subtask_id, workspace_id)
    if result.get("ok") and result.get("status") == "succeeded":
        merge_subagent_result(parent_task_id, subtask_id, workspace_id)
    child_session_id = result.get("child_session_id") or subtask_id
    return {
        "ok": result.get("ok", False) and result.get("status") == "succeeded",
        "final_response": result.get("summary", ""),
        "summary": result.get("summary", ""),
        "subtask_id": subtask_id,
        "child_session_id": child_session_id,
        "profile_id": profile_id,
        "agent_name": profile.name,
        "status": result.get("status", "unknown"),
        "findings": result.get("findings", []),
        "tool_results": result.get("tool_results", []),
        "errors": result.get("errors", []),
        "warnings": result.get("warnings", []),
    }


# ── Generic spawn dispatcher ─────────────────────────────────────────


def _spawn_agent(inv: ToolInvocation, profile_id: str, default_max_turns: int = 5) -> dict:
    """Generic dispatcher for spawning a subagent of a specific profile."""
    args = inv.arguments
    instruction = str(args.get("instruction", "")).strip()
    max_turns = int(args.get("max_turns", 0) or 0)
    background = bool(args.get("background", False))

    if not instruction:
        return _error_inv(inv, "instruction is required")

    profile = _get_profile(profile_id)
    if not profile:
        return _error_inv(inv, f"unknown profile_id: {profile_id}")

    workspace_id = _caller_workspace(inv)
    effective_turns = max_turns or default_max_turns

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
    return _spawn_agent(inv, profile_id=profile_id, default_max_turns=5)


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
    """Get subagent result by child_session_id."""
    args = inv.arguments
    ws = _caller_workspace(inv)
    child_session_id = str(args.get("child_session_id", "")).strip()

    if not child_session_id:
        return _error_inv(inv, "child_session_id is required")

    try:
        validate_workspace_id(ws)
        from storage.message_store import SessionMessageStore
        store = SessionMessageStore(session_id=child_session_id, ws_id=ws)
        if store.exists():
            messages = store.get_history_window(k=50)
            summary = {
                "child_session_id": child_session_id,
                "workspace_id": ws,
                "message_count": len(messages),
                "last_assistant_message": "",
                "tool_calls_count": 0,
            }
            for m in reversed(messages):
                if m.get("role") == "assistant":
                    summary["last_assistant_message"] = (m.get("content", "") or "")[:500]
                    break
            summary["tool_calls_count"] = sum(1 for m in messages if m.get("role") == "tool")
            return _ok(inv, "", summary)

        # Fall back to run records
        try:
            from storage.run_record_store import list_runs
            runs = list_runs(ws, session_id=child_session_id, limit=10)
            if runs:
                return _ok(inv, "", {
                    "child_session_id": child_session_id,
                    "workspace_id": ws,
                    "run_count": len(runs),
                    "runs": [{
                        "run_id": r.get("run_id", ""),
                        "ok": r.get("ok", False),
                        "summary": str(r.get("summary", ""))[:200],
                    } for r in runs],
                })
        except Exception:
            pass

        from agent.runtime.durable.subagent import get_subagent_task
        persisted = get_subagent_task(ws, child_session_id)
        if persisted is not None:
            return _ok(inv, "", {
                "child_session_id": child_session_id,
                "workspace_id": ws,
                **persisted,
            })

        return _ok(inv, "", {
            "child_session_id": child_session_id,
            "workspace_id": ws,
            "note": "no records found for this child session",
        })
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
    subtask_id = str(args.get("subtask_id") or args.get("child_session_id") or "").strip()
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
