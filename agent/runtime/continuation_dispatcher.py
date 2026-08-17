"""Durable, fail-closed dispatch of approved SSOT continuations.

A continuation remains ``ready`` until a background worker atomically claims it.
Submitting the worker is therefore retryable after a process restart; competing
submissions are harmless because only one worker can win the durable claim.
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading

_LOG = logging.getLogger(__name__)
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="approval-continuation",
)


def dispatch_ready_continuation(workspace_id: str, continuation_id: str) -> bool:
    """Queue a ready continuation without consuming its durable claim.

    ``True`` only means the worker was accepted by the local executor.  The
    worker performs the compare-and-swap claim before it can enter QueryLoop,
    which makes repeated API and reconciler submissions safe.
    """
    try:
        from storage.principal import bind_storage_principal
        _EXECUTOR.submit(
            bind_storage_principal(_claim_and_resume),
            workspace_id,
            continuation_id,
        )
        return True
    except RuntimeError:
        _LOG.warning(
            "continuation dispatch unavailable continuation=%s",
            continuation_id,
            exc_info=True,
        )
        return False


def _claim_and_resume(workspace_id: str, continuation_id: str) -> None:
    from agent.runtime.approval_continuation import claim_ready_continuation

    try:
        _record, grant, payload = claim_ready_continuation(
            workspace_id=workspace_id,
            continuation_id=continuation_id,
        )
    except Exception:  # noqa: BLE001 - durable worker boundary
        _LOG.exception("continuation claim failed continuation=%s", continuation_id)
        return
    if grant is None or payload is None:
        # Another worker won the claim, or the record was rejected/closed.
        return
    _resume_claimed_continuation(workspace_id, continuation_id, grant, payload)


def _resume_claimed_continuation(workspace_id: str, continuation_id: str, grant, payload) -> None:
    """Execute an already claimed continuation only through AgentApp/QueryLoop."""
    from agent.runtime.approval_continuation import (
        continuation_stall_seconds,
        finish_continuation,
        heartbeat_continuation,
        mark_continuation_dispatching,
    )
    from agent.runtime.session_events import push_error, push_turn_done

    session_id = str(payload.get("session_id") or "")
    heartbeat_stop = threading.Event()
    heartbeat_thread = None
    try:
        from agent.app.service import get_default_agent_app

        mark_continuation_dispatching(workspace_id, continuation_id)

        def _heartbeat() -> None:
            interval = max(10.0, min(60.0, continuation_stall_seconds() / 3))
            while not heartbeat_stop.wait(interval):
                try:
                    if not heartbeat_continuation(workspace_id, continuation_id):
                        return
                except (FileNotFoundError, OSError, RuntimeError, ValueError):
                    _LOG.warning(
                        "approval continuation heartbeat failed continuation=%s",
                        continuation_id,
                        exc_info=True,
                    )
                    return

        heartbeat_thread = threading.Thread(
            target=_heartbeat,
            name=f"approval-heartbeat-{continuation_id[-8:]}",
            daemon=True,
        )
        heartbeat_thread.start()
        resumed = get_default_agent_app().submit_user_message(
            user_input=str(payload.get("user_input") or ""),
            workspace_id=workspace_id,
            session_id=session_id,
            metadata={
                "__approved_tool_continuation": grant,
                "__approval_continuation_resume": True,
                "approval_parent_run_id": str(payload.get("parent_run_id") or ""),
                "__approval_cognitive_state": dict(payload.get("cognitive_state") or {}),
                "__approval_prior_tool_evidence": list(payload.get("prior_tool_evidence") or []),
                "transport": "approval_resume",
            },
        )
        completed = finish_continuation(
            workspace_id,
            continuation_id,
            completed_run_id=str(getattr(resumed, "turn_id", "") or ""),
            error="" if bool(getattr(resumed, "ok", False)) else "; ".join(
                list(getattr(resumed, "errors", None) or [])
            ),
        )
        if completed.get("status") == "completed":
            push_turn_done(
                session_id,
                str(getattr(resumed, "turn_id", "") or ""),
                str(getattr(resumed, "final_response", "") or ""),
                workspace_id=workspace_id,
            )
        else:
            push_error(
                session_id,
                "approval_resume_failed",
                str(completed.get("error") or "审批后的任务未完成"),
                workspace_id=workspace_id,
            )
    except Exception as exc:  # noqa: BLE001 - background boundary must persist failure
        _LOG.exception("approval continuation failed continuation=%s", continuation_id)
        finish_continuation(workspace_id, continuation_id, error=str(exc))
        push_error(
            session_id,
            "approval_resume_failed",
            "审批后的任务恢复失败，请查看运行记录。",
            workspace_id=workspace_id,
        )
    finally:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
