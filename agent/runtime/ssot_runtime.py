"""SSOT Runtime adapter for the public AgentApp turn contract.

This module is the bridge between the production-facing ``AgentResult``
contract and the SSOT Runtime execution engine. SSOT Runtime owns QueryLoop
planning, tool execution, bounded tracking, retry metadata, and result synthesis;
the actual tool boundary remains ``ToolRuntimeClient``
so manifest, policy, redaction and audit behavior are unchanged.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import time
from types import SimpleNamespace
from typing import Any

from agent.llm.schemas import LLMMessage
from agent.runtime.result import AgentResult
from agent.runtime.turn_persistence import persist_run_record
from agent.runtime.stream_emitter import build_trace_id
from agent.runtime.utils import now_iso
from agent.approval import get_approval_store
from core.runtime_engine.runtime_contracts import ExecutionContract

_LOG = logging.getLogger(__name__)
_MEMORY_WRITE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="ssot-memory-write",
)


_CALLER_RESERVED_RUNTIME_METADATA_KEYS = frozenset({
    "approval_parent_run_id",
    "approved_tool_call_ids",
    "approved_tool_call_keys",
    "approval_allowed",
    "approval_continuation_id",
    "approval_required",
    "approval_resolved",
    "cognitive_state",
    "conversation_history_block",
    "operational_clarification",
    "retrieved_context_block",
    "task_continuation_contract",
    "task_state_contract",
    "trusted_prompt_items",
    # Subagent controls become system-prompt text and tool-registry limits.
    # They are accepted only from SubagentRuntimeControl below.
    "subagent_profile",
    "max_steps",
    "subtask_id",
    "parent_session_id",
    "cancel_check",
})


def _sanitize_caller_runtime_metadata(metadata: Any) -> dict[str, Any]:
    """Keep caller metadata data-only before it enters the SSOT control plane."""
    if not isinstance(metadata, dict):
        return {}
    return {
        str(key): value
        for key, value in metadata.items()
        if isinstance(key, str)
        and not key.startswith("__")
        and key not in _CALLER_RESERVED_RUNTIME_METADATA_KEYS
    }


def _is_approved_continuation_runtime_control(runtime_control: Any) -> bool:
    """Return true only for a server-created typed approval continuation."""
    from core.runtime_engine.models import (
        ApprovedContinuationRuntimeControl,
        ApprovedToolContinuation,
    )

    return (
        isinstance(runtime_control, ApprovedContinuationRuntimeControl)
        and isinstance(runtime_control.grant, ApprovedToolContinuation)
    )


def _apply_runtime_control(metadata: dict[str, Any], runtime_control: Any) -> None:
    """Install only typed, server-created runtime control envelopes."""
    if _is_approved_continuation_runtime_control(runtime_control):
        metadata.update({
            "__approved_tool_continuation": runtime_control.grant,
            "__approval_continuation_resume": True,
            "approval_parent_run_id": str(runtime_control.parent_run_id or ""),
            "__approval_cognitive_state": dict(runtime_control.cognitive_state or {}),
            "__approval_prior_tool_evidence": list(runtime_control.prior_tool_evidence or ()),
        })
        return

    from core.runtime_engine.models import MainAgentRuntimeControl, SubagentRuntimeControl
    if isinstance(runtime_control, MainAgentRuntimeControl):
        if callable(runtime_control.cancel_check):
            metadata["cancel_check"] = runtime_control.cancel_check
        return
    if not isinstance(runtime_control, SubagentRuntimeControl):
        return
    profile = runtime_control.profile if isinstance(runtime_control.profile, dict) else {}
    metadata.update({
        "subagent_profile": dict(profile),
        "max_steps": max(1, int(runtime_control.max_steps or 1)),
        "subtask_id": str(runtime_control.subtask_id or ""),
        "parent_session_id": str(runtime_control.parent_session_id or ""),
    })
    if callable(runtime_control.cancel_check):
        metadata["cancel_check"] = runtime_control.cancel_check


class _TaskStateResolutionFailure(RuntimeError):
    """Internal marker for a fail-closed TaskState read boundary."""


def _persist_inflight_user_message(session, turn, user_input: str) -> None:
    """Durably retain the original request before tools can run.

    The terminal run persistence writes the same `(run_id, user)` projection
    again, so this is idempotent.  An interrupted process therefore restores a
    safe untrusted context for an explicit later resume.
    """
    if not user_input or _is_approved_continuation_runtime_control(
        getattr(turn.op, "runtime_control", None)
    ):
        return
    from storage.message_store import SessionMessageStore
    from core.runtime_engine.context_compaction import build_history_state_record
    from agent.runtime.message_identity import user_message_storage_run_id
    metadata = dict(getattr(turn.op, "metadata", {}) or {})
    message_run_id = user_message_storage_run_id(
        str(metadata.get("client_request_id") or ""), turn.turn_id,
    )
    SessionMessageStore(session_id=session.session_id, ws_id=session.workspace_id).write_message(
        message_run_id,
        "user",
        user_input,
        metadata={
            "created_at": now_iso(),
            "client_request_id": str(metadata.get("client_request_id") or ""),
            "attachments": list(metadata.get("attachments") or []),
            "history_state": build_history_state_record(
                "user", user_input, references=list(metadata.get("attachments") or [])
            ),
        },
    )


def _mark_task_state_persistence_failure(result: AgentResult, code: str) -> None:
    """Prevent an uncommitted execution from being reported as task completion."""
    result.ok = False
    result.error_type = code
    if code not in result.errors:
        result.errors.append(code)
    result.metadata.setdefault(
        "task_state_persistence",
        {"stage": "commit", "status": "failed", "code": code},
    )
    result.final_response = (
        "本轮执行结果未能写入可信任务状态，系统已将其标记为未完成。"
        "为避免重复或遗漏操作，请先恢复任务状态；不要据此回复继续执行副作用操作。"
    )



def _mark_run_record_persistence_failure(result: AgentResult, code: str) -> None:
    """Prevent a turn without its primary durable record from being reported as successful."""
    result.ok = False
    result.error_type = code
    if code not in result.errors:
        result.errors.append(code)
    result.metadata.setdefault(
        "run_record_persistence",
        {"stage": "persist", "status": "failed", "code": code},
    )
    result.final_response = (
        "本轮执行结果未能写入可信运行记录，系统已将其标记为未完成。"
        "为避免重复或遗漏操作，请恢复存储后重新核验任务状态。"
    )

def run_ssot_turn(
    session,
    turn,
    *,
    allowed_tool_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    requested_by: str = "turn_runner",
    emitter: Any | None = None,
) -> AgentResult:
    """Run one user turn through SSOT Runtime and return the stable AgentResult.

    Args:
        emitter: Optional StreamEmitter (or any object exposing ``emit(event_type, payload)``)
            used by SSOT Runtime to publish per-stage progress events to the WebSocket
            real-time callback. When omitted, SSOT Runtime runs without progress signals
            (used by offline tests / replay tools).
    """
    started = time.monotonic()
    trace_id = build_trace_id()
    workspace_id = getattr(session, "workspace_id", "") or getattr(turn.op, "workspace_id", "")
    session_id = getattr(session, "session_id", "") or getattr(turn.op, "session_id", "")
    user_input = (getattr(turn.op, "user_input", "") or "").strip()
    metadata_in = _sanitize_caller_runtime_metadata(
        getattr(turn.op, "metadata", {}) or {}
    )
    _apply_runtime_control(metadata_in, getattr(turn.op, "runtime_control", None))
    task_continuation_contract: dict[str, Any] | None = None

    try:
        from backend.core.chat_attachments import build_attachment_runtime_guidance
        # Follow-up turns do not repeat an upload. Keep the latest managed file
        # references available as trusted runtime metadata so the model can reuse
        # the FileStore id instead of hallucinating a transient workspace path.
        current_attachments = list(metadata_in.get("attachments") or [])
        historical_attachments = (
            [] if current_attachments else _recent_session_attachments(
                session,
                user_input=user_input,
            )
        )
        known_attachments = _active_attachment_references(
            workspace_id,
            _merge_attachment_references(
            current_attachments,
            historical_attachments,
            ),
        )
        if known_attachments:
            metadata_in["attachments"] = known_attachments
        attachment_guidance = build_attachment_runtime_guidance(known_attachments)
        if attachment_guidance:
            from core.runtime_engine.prompt_contract import trusted_prompt_item
            metadata_in.setdefault("trusted_prompt_items", []).append(
                trusted_prompt_item("managed_attachment", attachment_guidance)
            )
    except Exception:
        _LOG.warning("attachment runtime guidance preparation failed", exc_info=True)

    # Build the full LLM-visible tool registry first. RuntimeContextBudget
    # deducts its schema cost before assigning history/retrieval capacity.
    ssot_registry = _build_ssot_runtime_tool_registry(allowed_tool_ids)
    runtime_context_budget = _build_runtime_context_budget(ssot_registry)

    # ── Build canonical conversation context for prompt injection ──
    metadata_in["__raw_user_input"] = user_input
    history_exclude_run_id = (
        str(metadata_in.get("approval_parent_run_id") or "")
        if metadata_in.get("__approval_continuation_resume") else ""
    )
    history_exclude_client_request_id = str(metadata_in.get("client_request_id") or "")
    history_block = _build_history_block(
        session,
        user_input=user_input,
        max_tokens=runtime_context_budget.history_tokens,
        exclude_run_id=history_exclude_run_id,
        exclude_client_request_id=history_exclude_client_request_id,
    )
    if history_block:
        metadata_in["conversation_history_block"] = history_block
    # One server-filtered history projection feeds every state resolver. This
    # prevents current pre-written requests from becoming continuation history.
    context_messages = _load_context_messages(
        session,
        exclude_run_id=history_exclude_run_id,
        exclude_client_request_id=history_exclude_client_request_id,
    )
    # Session task continuation is a separate SSOT. Only server-derived
    # relation/progress fields enter trusted guidance; historic user wording
    # remains untrusted in the conversation history block.
    try:
        from agent.runtime.task_continuation import (
            render_task_continuation_guidance,
            resolve_task_continuation,
        )
        from core.runtime_engine.prompt_contract import trusted_prompt_item

        task_continuation_contract = resolve_task_continuation(
            workspace_id=workspace_id,
            session_id=session_id,
            user_input=user_input,
            messages=context_messages,
        )
        if task_continuation_contract:
            metadata_in["task_continuation_contract"] = task_continuation_contract
            metadata_in.setdefault("trusted_prompt_items", []).append(
                trusted_prompt_item(
                    "task_continuation",
                    render_task_continuation_guidance(task_continuation_contract),
                )
            )
    except Exception:
        _LOG.warning("task continuation resolution failed", exc_info=True)

    # Generic TaskState is a trusted runtime projection, not a second planner.
    # It carries only server-derived lifecycle facts into the canonical QueryLoop.
    task_state_contract: dict[str, Any] | None = None
    task_state_resolution_error: Exception | None = None
    approval_parent_run_id = (
        str(metadata_in.get("approval_parent_run_id") or "")
        if metadata_in.get("__approval_continuation_resume") else ""
    )
    try:
        from agent.runtime.task_state import render_task_state_guidance, resolve_task_state
        from core.runtime_engine.prompt_contract import trusted_prompt_item
        task_state_contract = resolve_task_state(
            workspace_id=workspace_id,
            session_id=session_id,
            user_input=user_input,
            messages=context_messages,
            approval_parent_run_id=approval_parent_run_id,
        )
        if task_state_contract:
            from agent.runtime.task_state import acknowledge_pending_mutation_outcome
            acknowledged_contract = acknowledge_pending_mutation_outcome(
                workspace_id=workspace_id,
                session_id=session_id,
                run_id=turn.turn_id,
                contract=task_state_contract,
                user_input=user_input,
            )
            if acknowledged_contract is not None:
                task_state_contract = acknowledged_contract
            metadata_in["task_state_contract"] = task_state_contract
            metadata_in["__trusted_task_state_contract"] = task_state_contract
            metadata_in.setdefault("trusted_prompt_items", []).append(
                trusted_prompt_item(
                    "task_state",
                    render_task_state_guidance(task_state_contract),
                )
            )
    except Exception as exc:
        # Generic task state is a safety-relevant SSOT contract.  Continuing as
        # an untracked turn can repeat a prior mutation or falsely complete a
        # replan, so defer a fail-closed result until the common context exists.
        task_state_resolution_error = exc
        _LOG.warning("task state resolution failed", exc_info=True)
    if task_state_resolution_error is None:
        try:
            from agent.runtime.task_state import begin_task_state
            active_task_contract = begin_task_state(
                workspace_id=workspace_id,
                session_id=session_id,
                run_id=turn.turn_id,
                user_input=user_input,
                continuation_contract=task_state_contract,
            )
            if active_task_contract is None:
                raise RuntimeError("task_state_begin_rejected")
            task_state_contract = active_task_contract
            metadata_in["task_state_contract"] = active_task_contract
            metadata_in["__trusted_task_state_contract"] = active_task_contract
            trusted_items = list(metadata_in.get("trusted_prompt_items") or [])
            metadata_in["trusted_prompt_items"] = [
                item for item in trusted_items
                if getattr(item, "source_kind", "") != "task_state"
            ]
            from core.runtime_engine.prompt_contract import trusted_prompt_item
            from agent.runtime.task_state import render_task_state_guidance
            metadata_in["trusted_prompt_items"].append(
                trusted_prompt_item("task_state", render_task_state_guidance(active_task_contract))
            )
            from agent.runtime.task_state import checkpoint_task_state_execution
            def _task_state_execution_checkpoint(phase: str, manifest: list[dict[str, Any]]):
                nonlocal task_state_contract
                next_contract = checkpoint_task_state_execution(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    run_id=turn.turn_id,
                    contract=dict(task_state_contract or {}),
                    phase=phase,
                    manifest=manifest,
                )
                if next_contract is None:
                    raise RuntimeError("task_state_execution_checkpoint_rejected")
                task_state_contract = next_contract
                metadata_in["task_state_contract"] = next_contract
                metadata_in["__trusted_task_state_contract"] = next_contract
                return next_contract
            metadata_in["__task_state_execution_checkpoint"] = _task_state_execution_checkpoint
        except Exception as exc:
            task_state_resolution_error = exc
            _LOG.warning("task state begin checkpoint failed", exc_info=True)
    retrieved_context_block = _build_retrieved_context_block(
        workspace_id=workspace_id,
        session_id=session_id,
        task_id=turn.turn_id,
        user_input=user_input,
        max_tokens=runtime_context_budget.retrieved_context_tokens,
        include_workspace_memory=not bool(
            getattr(session, "is_sub_agent", False)
        ),
    )
    if retrieved_context_block:
        metadata_in["retrieved_context_block"] = retrieved_context_block

    metadata_in["runtime_context_budget"] = runtime_context_budget.as_dict()

    context = SimpleNamespace(
        workspace_id=workspace_id,
        session_id=session_id,
        turn_id=turn.turn_id,
        trace_id=trace_id,
        requested_by=requested_by,
        metadata={
            "runtime_engine": "ssot_runtime",
            "transport": metadata_in.get("transport", ""),
            "stream_mode": metadata_in.get("stream_mode", ""),
            "intent": "assistant_chat",
            "visible_tools": sorted(ssot_registry.keys()),
            "requested_by": requested_by,
        },
    )

    events: list[dict[str, Any]] = [
        _event("turn_start", "轮次开始", trace_id, turn.turn_id, started_at=started),
        _event("model", "model", trace_id, turn.turn_id, started_at=started),
    ]

    try:
        if task_state_resolution_error is not None:
            raise _TaskStateResolutionFailure() from task_state_resolution_error
        _persist_inflight_user_message(session, turn, user_input)
        engine = _build_engine(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=turn.turn_id,
            trace_id=trace_id,
            allowed_tool_ids=allowed_tool_ids,
            requested_by=requested_by,
            emitter=emitter,
            prebuilt_registry=ssot_registry,
            max_query_loop_iterations=metadata_in.get("max_steps"),
            max_tool_nodes=metadata_in.get("max_steps"),
            context_budget=runtime_context_budget,
            approved_tool_grant=metadata_in.get("__approved_tool_continuation"),
            approval_run_id=(
                str(metadata_in.get("approval_parent_run_id") or "")
                if metadata_in.get("__approval_continuation_resume")
                else ""
            ),
        )
        runtime_result = _run_async(
            engine.run(
                user_input=user_input,
                workspace_id=workspace_id,
                session_id=session_id,
                extras=metadata_in,
            )
        )

        tool_calls = _project_tool_calls(runtime_result)
        final_response = _final_response(runtime_result)
        if not final_response:
            if tool_calls:
                final_response = _tool_result_fallback_from_projected_calls(tool_calls)
            else:
                final_response = "抱歉，服务暂时无法处理您的请求，请稍后重试。"
        events.extend(_project_events(runtime_result, trace_id, turn.turn_id))
        events.append(_event("final", "final", trace_id, turn.turn_id, started_at=started))

        timeline_summary = _timeline_summary(
            started=started,
            events=events,
            tool_calls=tool_calls,
            runtime_result=runtime_result,
        )
        # QueryLoop exposes a terminal error both as a primary `error` field
        # and, for multi-error paths, as `errors`. Preserve the ordered union
        # before projecting lifecycle facts; otherwise cancellation is visible
        # to the response but lost to TaskState terminal derivation.
        runtime_errors = [
            str(item)[:240]
            for item in (runtime_result.errors or [])
            if str(item).strip()
        ]
        terminal_error = str(getattr(runtime_result, "error", "") or "").strip()
        if terminal_error and terminal_error not in runtime_errors:
            runtime_errors.append(terminal_error[:240])
        runtime_errors = runtime_errors[:16]
        metadata = {
            **context.metadata,
            "runtime_engine": "ssot_runtime",
            "ssot_runtime": runtime_result.metadata,
            "timeline_summary": timeline_summary,
            "steps": 1,
            "model": _current_model_name(),
            "llm": {
                "used": True,
                "provider": _current_provider_name(),
                "model": _current_model_name(),
                "task": "assistant_chat",
            },
            # v3.10 (tool retry): top-level projections so the
            # frontend / API consumers don't have to walk through
            # ``metadata.runtime.*`` to find the retry surface. The
            # canonical source stays inside ``metadata.runtime``; the
            # top-level fields are read-only mirrors maintained for
            # convenience. If both fields are present they MUST be
            # byte-identical.
            "retry_summary": dict(
                (runtime_result.metadata or {}).get("retry_summary")
                or {
                    "retry_attempts": 0,
                    "retried_nodes": [],
                    "retry_succeeded": 0,
                    "retry_failed": 0,
                    "retry_blocked": 0,
                },
            ),
            "retry_events": list(
                (runtime_result.metadata or {}).get("retry_events") or []
            ),
            "validation_correction_summary": dict(
                (runtime_result.metadata or {}).get("validation_correction_summary") or {}
            ),
            "validation_correction_events": list(
                (runtime_result.metadata or {}).get("validation_correction_events") or []
            ),
            "tool_recovery_events": list(
                (runtime_result.metadata or {}).get("tool_recovery_events") or []
            ),
            "orchestration_batches": list(
                (runtime_result.metadata or {}).get("orchestration_batches") or []
            ),
            "tracking_summary": dict(
                (runtime_result.metadata or {}).get("tracking_summary") or {}
            ),
            "tracking_events": list(
                (runtime_result.metadata or {}).get("tracking_events") or []
            ),
            "context_compacted": bool((runtime_result.metadata or {}).get("context_compacted", False)),
            "context_estimated_tokens": int(
                (runtime_result.metadata or {}).get("context_estimated_tokens", 0) or 0
            ),
            "context_budget": dict((runtime_result.metadata or {}).get("context_budget") or {}),
            "output_truncated": bool((runtime_result.metadata or {}).get("output_truncated", False)),
            "output_truncation_reason": str(
                (runtime_result.metadata or {}).get("output_truncation_reason") or ""
            ),
            "execution_outcome": str(
                (runtime_result.metadata or {}).get("execution_outcome")
                or ("complete" if runtime_result.success else "failed")
            ),
            # Server-produced terminal errors are lifecycle facts for TaskState;
            # request metadata never contributes to this list.
            "runtime_errors": list(runtime_errors),
            "tool_execution_outcome": str(
                (runtime_result.metadata or {}).get("tool_execution_outcome")
                or ("complete" if runtime_result.success and not runtime_errors else "failed")
            ),
            # Read-only terminal facts for API/UI consumers. QueryLoop remains
            # the only owner of execution, recovery and write fencing.
            "unknown_outcome": (
                dict((runtime_result.metadata or {}).get("unknown_outcome") or {})
                if isinstance((runtime_result.metadata or {}).get("unknown_outcome"), dict)
                else {}
            ),
            "goal_assertions": (
                dict((runtime_result.metadata or {}).get("goal_assertions") or {})
                if isinstance((runtime_result.metadata or {}).get("goal_assertions"), dict)
                else {}
            ),
            "evidence": dict((runtime_result.metadata or {}).get("evidence") or {}),
            # Read-only CognitiveState projection from the SSOT QueryLoop.
            # The adapter mirrors server-owned fields only; request metadata is
            # never consulted for cognitive state.
            "cognitive": (
                dict((runtime_result.metadata or {}).get("cognitive") or {})
                if isinstance((runtime_result.metadata or {}).get("cognitive"), dict)
                else {}
            ),
            "cognitive_events": (
                list((runtime_result.metadata or {}).get("cognitive_events") or [])
                if isinstance((runtime_result.metadata or {}).get("cognitive_events"), list)
                else []
            ),
        }
        failed_tool_count = sum(1 for call in tool_calls if not call.get("ok"))
        successful_tool_count = len(tool_calls) - failed_tool_count
        if not runtime_result.success and failed_tool_count and not runtime_errors:
            runtime_errors.append("all_tool_calls_failed")
        runtime_warnings = []
        if failed_tool_count and successful_tool_count:
            runtime_warnings.append(
                f"partial_tool_failure: {failed_tool_count} failed, {successful_tool_count} succeeded"
            )

        result = AgentResult(
            ok=bool(runtime_result.success),
            final_response=final_response,
            events=events,
            trace_id=trace_id,
            session_id=session_id,
            turn_id=turn.turn_id,
            tool_calls=tool_calls,
            warnings=runtime_warnings,
            errors=runtime_errors,
            metadata=metadata,
            error_type="" if runtime_result.success else "ssot_runtime_error",
            tool_decision=_tool_decision(runtime_result, tool_calls),
            no_tool_reason="" if tool_calls else "SSOT Runtime planner selected no tools.",
        )

    except _TaskStateResolutionFailure:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        events.append(_event("error", "TaskState resolution failed", trace_id, turn.turn_id, started_at=started))
        result = AgentResult(
            ok=False,
            final_response=(
                "任务状态无法读取，系统已安全停止，未执行模型或工具。"
                "请稍后重试；不要把本轮视为已完成。"
            ),
            events=events,
            trace_id=trace_id,
            session_id=session_id,
            turn_id=turn.turn_id,
            errors=["task_state_resolution_failed"],
            metadata={
                **context.metadata,
                "runtime_engine": "ssot_runtime",
                "execution_outcome": "failed",
                "tool_execution_outcome": "failed",
                "task_state_persistence": {"stage": "resolution", "status": "failed"},
                "timeline_summary": {
                    "node_count": len(events),
                    "total_duration_ms": elapsed_ms,
                    "artifact_saved_count": 0,
                },
            },
            error_type="task_state_resolution_failed",
            tool_decision={"needed": False, "reason": "TaskState resolution failed before execution."},
            no_tool_reason="task_state_resolution_failed",
        )
    except Exception as exc:
        _LOG.exception("SSOT Runtime turn failed")
        from storage.redaction import redact_text
        safe_error = redact_text(str(exc))[:500] or "ssot_runtime_error"
        elapsed_ms = int((time.monotonic() - started) * 1000)
        events.append(_event("error", "SSOT Runtime error", trace_id, turn.turn_id, started_at=started))
        result = AgentResult(
            ok=False,
            final_response="运行时处理失败，系统已安全停止。请稍后重试。",
            events=events,
            trace_id=trace_id,
            session_id=session_id,
            turn_id=turn.turn_id,
            errors=[safe_error],
            metadata={
                **context.metadata,
                "runtime_engine": "ssot_runtime",
                "execution_outcome": "failed",
                "tool_execution_outcome": "failed",
                "timeline_summary": {
                    "node_count": len(events),
                    "total_duration_ms": elapsed_ms,
                    "artifact_saved_count": 0,
                },
            },
            error_type="ssot_runtime_error",
            tool_decision={"needed": False, "reason": "SSOT Runtime failed before execution."},
            no_tool_reason="ssot_runtime_error",
        )

    # ── Section 2: unified exit — sync session.history for both success
    #    and exception paths so the next turn always has context.
    is_approval_resume = bool(metadata_in.get("__approval_continuation_resume"))
    is_approval_pending = bool(
        (result.metadata or {}).get("ssot_runtime", {}).get("approval_required")
    )
    _sync_session_history(
        session,
        "" if is_approval_resume else user_input,
        result.final_response,
        include_user=not is_approval_resume,
        include_assistant=not is_approval_pending,
        run_id=turn.turn_id,
        client_request_id=history_exclude_client_request_id,
    )
    task_state_commit_error = ""
    try:
        from agent.runtime.task_state import commit_task_state
        task_state_snapshot = commit_task_state(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=turn.turn_id,
            user_input=user_input,
            final_response=result.final_response or "",
            run_ok=bool(result.ok),
            runtime_metadata=_task_state_runtime_metadata(result.metadata),
            tool_calls=list(result.tool_calls or []),
            continuation_contract=task_state_contract,
        )
        if task_state_snapshot and isinstance(result.metadata, dict):
            result.metadata["task_state"] = task_state_snapshot
        elif task_state_snapshot is None:
            # A None result includes a stale continuation CAS.  Do not expose a
            # seemingly completed response whose canonical task state did not
            # advance.
            task_state_commit_error = "task_state_commit_rejected"
    except Exception:
        task_state_commit_error = "task_state_commit_failed"
        _LOG.warning("task state commit failed", exc_info=True)
    if task_state_commit_error:
        _mark_task_state_persistence_failure(result, task_state_commit_error)
    elif not is_approval_pending:
        # Enumerated delivery continuation is a secondary, domain-specific
        # projection.  It may advance only after the generic TaskState SSOT
        # accepted the same terminal turn; otherwise the two stores diverge.
        try:
            from agent.runtime.task_continuation import commit_task_continuation

            task_snapshot = commit_task_continuation(
                workspace_id=workspace_id,
                session_id=session_id,
                run_id=turn.turn_id,
                user_input=user_input,
                assistant_response=result.final_response or "",
                run_ok=bool(result.ok),
                continuation_contract=task_continuation_contract,
            )
            if task_snapshot and isinstance(result.metadata, dict):
                result.metadata["task_continuation"] = task_snapshot
        except Exception:
            _LOG.warning("task continuation commit failed", exc_info=True)

    run_record_persisted = persist_run_record(session, turn, result, context)
    if run_record_persisted is False:
        _mark_run_record_persistence_failure(result, "run_record_persistence_failed")

    # ── Experience journal and memory reflection ─────────────────────
    # Every completed turn is durable experience. Explicit user memory
    # commands are applied immediately; ordinary turns are consolidated only
    # at an operational task boundary or after a small accumulated batch.
    if run_record_persisted is not False and not bool((result.metadata or {}).get("ssot_runtime", {}).get("approval_required")):
        _record_experience_and_maybe_reflect(
            workspace_id=workspace_id,
            session_id=session_id,
            task_id=turn.turn_id,
            user_input=user_input,
            assistant_response=result.final_response or "",
            tool_calls=list(result.tool_calls or []),
            task_ok=bool(result.ok),
        )

    return result


def _task_state_runtime_metadata(result_metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Project only server-owned terminal facts into the TaskState commit.

    ``ssot_runtime`` preserves raw QueryLoop metrics, while the adapter may
    normalize lifecycle fields (notably a primary QueryLoop ``error`` into
    ``runtime_errors``).  Passing the raw mirror alone splits TaskState from the
    delivered AgentResult.  The fixed allow-list preserves the execution facts
    without allowing caller-provided top-level metadata to influence state.
    """
    metadata = result_metadata if isinstance(result_metadata, dict) else {}
    raw = metadata.get("ssot_runtime")
    projected = dict(raw) if isinstance(raw, dict) else {}
    for key in (
        "execution_outcome",
        "runtime_errors",
        "tool_execution_outcome",
        "unknown_outcome",
        "goal_assertions",
        "evidence",
        "cognitive",
        "cognitive_events",
    ):
        if key in metadata:
            projected[key] = metadata[key]
    return projected


def _record_experience_and_maybe_reflect(
    *,
    workspace_id: str,
    session_id: str,
    task_id: str,
    user_input: str,
    assistant_response: str,
    tool_calls: list[dict[str, Any]],
    task_ok: bool,
) -> None:
    try:
        from agent.runtime.memory_hooks import install_memory_governance_hooks
        from agent.runtime.memory_write.commands import apply_memory_command, parse_memory_command
        from agent.runtime.memory_write.consolidator import consolidate_experiences, should_consolidate
        from agent.runtime.memory_write.event_log import (
            append_experience,
            mark_experiences_processed,
            pending_experiences,
        )
        from storage.memory_governance import is_auto_memory_enabled

        install_memory_governance_hooks()
        if not is_auto_memory_enabled(workspace_id):
            return
        event = append_experience(
            workspace_id=workspace_id,
            session_id=session_id,
            task_id=task_id,
            user_input=user_input,
            assistant_response=assistant_response,
            tool_calls=tool_calls,
            task_ok=task_ok,
        )
        command = parse_memory_command(user_input)
        if command is not None:
            apply_memory_command(
                command,
                workspace_id=workspace_id,
                session_id=session_id,
                task_id=task_id,
            )
            mark_experiences_processed(workspace_id, session_id, [str(event.get("event_id") or "")])
            return
        pending = pending_experiences(workspace_id, session_id, limit=12)
        if should_consolidate(pending):
            from storage.principal import bind_storage_principal
            future = _MEMORY_WRITE_EXECUTOR.submit(
                bind_storage_principal(consolidate_experiences),
                workspace_id=workspace_id,
                session_id=session_id,
                task_id=task_id,
            )
            future.add_done_callback(_log_memory_reflection_failure)
    except Exception:
        _LOG.warning("experience journal write failed", exc_info=True)


def _log_memory_reflection_failure(done: concurrent.futures.Future) -> None:
    try:
        done.result()
    except Exception:
        _LOG.warning("background memory reflection failed", exc_info=True)


def _build_engine(
    *,
    workspace_id: str,
    session_id: str,
    run_id: str,
    trace_id: str,
    allowed_tool_ids=None,
    requested_by: str,
    emitter: Any | None = None,
    prebuilt_registry: dict[str, dict[str, Any]] | None = None,
    max_query_loop_iterations: int | None = None,
    max_tool_nodes: int | None = None,
    context_budget=None,
    approved_tool_grant=None,
    approval_run_id: str = "",
):
    from core.runtime_engine import SSOTRuntimeConfig, SSOTRuntimeEngine
    from core.runtime_engine.tool_runtime import ToolRuntime

    config = SSOTRuntimeConfig(
        max_global_concurrency=8,
        max_layer_concurrency=5,
        max_llm_calls=50,
        max_total_seconds=180,
        max_tool_seconds=120,
        single_node_timeout_ms=120_000,
        parallel_layer_timeout_ms=300_000,
        tracking_max_seconds=150,
        tracking_max_polls=40,
        tracking_poll_interval_cap_seconds=5,
        max_query_loop_iterations=max(
            1,
            min(int(max_query_loop_iterations or 20), 20),
        ),
        max_nodes=max(1, min(int(max_tool_nodes or 30), 30)),
        max_tool_calls_per_iteration=max(
            1, min(int(max_tool_nodes or 8), 8),
        ),
        context_window_tokens=int(getattr(context_budget, "context_window_tokens", 0) or 0),
        max_input_tokens=int(getattr(context_budget, "max_input_tokens", 48_000) or 48_000),
        max_output_tokens=int(getattr(context_budget, "reserved_output_tokens", 4096) or 4096),
        context_safety_tokens=int(getattr(context_budget, "safety_tokens", 2048) or 2048),
    )
    registry = prebuilt_registry or _build_ssot_runtime_tool_registry(allowed_tool_ids)
    client = _tool_runtime_client()
    engine_kwargs: dict[str, Any] = {
        "config": config,
        "llm_invoke": _invoke_llm_for_ssot_runtime,
        "tool_registry": registry,
        "tool_runtime": ToolRuntime(config),
    }
    if emitter is not None:
        engine_kwargs["emitter"] = emitter
    engine_kwargs["approval_handler"] = _build_approval_handler(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        emitter=emitter,
        client=client,
    )
    engine = SSOTRuntimeEngine(**engine_kwargs)
    approved_call_grants = _approved_call_grants(approved_tool_grant)

    for tool_id in registry:
        engine.register_tool(
            tool_id,
            _make_tool_handler(
                client=client,
                tool_id=tool_id,
                workspace_id=workspace_id,
                session_id=session_id,
                run_id=run_id,
                approval_run_id=approval_run_id or "",
                trace_id=trace_id,
                requested_by=requested_by,
                approved_call_grants=approved_call_grants,
            ),
            description=registry[tool_id].get("description", ""),
            args_schema=registry[tool_id].get("args_schema", {}),
        )
    return engine


def _build_approval_handler(
    *,
    workspace_id: str,
    session_id: str,
    run_id: str,
    emitter: Any | None = None,
    client: Any | None = None,
):
    """Persist an ordinary Agent approval continuation without blocking a worker."""
    client = client or _tool_runtime_client()

    async def _handle(ctx, gate: dict[str, Any]) -> dict[str, Any]:
        from agent.approval import new_approval_id
        from agent.runtime.approval_continuation import (
            create_continuation,
            delete_continuation,
            new_continuation_id,
        )

        store = get_approval_store(workspace_id)
        tool_calls = [dict(item) for item in list(gate.get("tool_calls") or []) if isinstance(item, dict)]
        if not tool_calls:
            raise RuntimeError("approval continuation requires exact tool calls")
        raw_details = list(gate.get("approval_details") or [])
        approval_nodes = [str(node_id) for node_id in list(gate.get("approval_nodes") or []) if str(node_id)]
        if not approval_nodes or len(set(approval_nodes)) != len(approval_nodes):
            raise RuntimeError("approval continuation requires unique approval nodes")
        calls_by_node_id: dict[str, dict[str, Any]] = {}
        for call in tool_calls:
            node_id = str(call.get("id") or "")
            if node_id:
                calls_by_node_id[node_id] = call
        missing_calls = [node_id for node_id in approval_nodes if node_id not in calls_by_node_id]
        if missing_calls:
            raise RuntimeError("approval continuation nodes missing exact tool calls: " + ", ".join(missing_calls))
        details_by_node_id: dict[str, dict[str, Any]] = {}
        for raw_detail in raw_details:
            if not isinstance(raw_detail, dict):
                raise RuntimeError("approval detail must be an object")
            detail = dict(raw_detail)
            node_id = str(detail.get("node_id") or "")
            # Older single-node gates did not carry a node_id.  Do not infer an
            # ordering for batches: it would bind the wrong user decision.
            if not node_id and len(approval_nodes) == 1 and len(raw_details) == 1:
                node_id = approval_nodes[0]
            if not node_id or node_id not in calls_by_node_id or node_id in details_by_node_id:
                raise RuntimeError("approval details must map uniquely to approval nodes")
            details_by_node_id[node_id] = detail
        # Bind the durable approval to the same default-expanded parameters
        # that the final ToolRuntimeClient will pass to ToolExecutor.
        for node_id in approval_nodes:
            call = calls_by_node_id[node_id]
            tool_id = str(call.get("name") or "").replace("__", ".")
            if not tool_id:
                raise RuntimeError(f"approval node {node_id} has no canonical tool id")
            call["name"] = tool_id
            call["arguments"] = client.canonicalize_arguments(
                tool_id,
                call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
            )

        approval_ids = [new_approval_id() for _ in approval_nodes]
        continuation_id = new_continuation_id()
        cognitive_state = ctx.extras.get("cognitive_state")
        cognitive_snapshot = (
            cognitive_state.as_trace_payload()
            if hasattr(cognitive_state, "as_trace_payload")
            else {}
        )
        prior_tool_evidence = list(ctx.extras.get("__approval_prior_tool_evidence") or [])
        continuation_id = create_continuation(
            workspace_id=workspace_id,
            session_id=session_id,
            parent_run_id=run_id,
            user_input=str(ctx.user_input or ""),
            tool_calls=tool_calls,
            approval_ids=approval_ids,
            approved_node_ids=approval_nodes,
            cognitive_state=cognitive_snapshot,
            prior_tool_evidence=prior_tool_evidence,
            continuation_id=continuation_id,
        )
        specs: list[dict[str, Any]] = []
        for approval_id, node_id in zip(approval_ids, approval_nodes):
            call = calls_by_node_id[node_id]
            tool_id = str(call.get("name") or "")
            arguments = dict(call.get("arguments") or {})
            if not tool_id:
                raise RuntimeError(f"approval node {node_id} has no canonical tool id")
            detail = details_by_node_id.get(node_id, {})
            reason = str(detail.get("risk_reason") or gate.get("message") or "高危操作需要确认")
            command = str(arguments.get("command") or detail.get("command") or "")
            description = f"{reason}: {tool_id}"
            if command:
                description += f" → {command[:120]}"
            specs.append({
                "approval_id": approval_id,
                "session_id": session_id,
                # The durable approval binding is the exact canonical call, not
                # a lossy risk detail nor a position in a details list.
                "tool_id": tool_id,
                "arguments": arguments,
                "description": description,
                "risk_level": str(gate.get("risk_level") or "high"),
                "workspace_id": workspace_id,
                "run_id": run_id,
                "metadata": {"continuation_id": continuation_id, "node_id": node_id},
            })

        try:
            store.create_batch(specs)
        except Exception:
            delete_continuation(workspace_id, continuation_id)
            raise
        event = {
            "approval_ids": approval_ids,
            "continuation_id": continuation_id,
            "risk_level": str(gate.get("risk_level") or "high"),
            "status": "pending",
        }
        ctx.extras.setdefault("approval_events", []).append(event)
        if emitter is not None:
            emitter.emit("approval_waiting", event)

        return {
            "status": "pending",
            "approval_ids": approval_ids,
            "continuation_id": continuation_id,
        }

    return _handle


def _build_ssot_runtime_tool_registry(allowed_tool_ids=None) -> dict[str, dict[str, Any]]:
    client = _tool_runtime_client()
    tools = {}
    allowed = set(allowed_tool_ids or []) if allowed_tool_ids else None
    action_profiles = {}
    try:
        from core.tools.catalog_snapshot import build_catalog_snapshot
        action_profiles = {
            item.get("tool_id"): item.get("action_profiles", [])
            for item in build_catalog_snapshot().get("tools", [])
            if isinstance(item, dict)
        }
    except Exception:
        action_profiles = {}
    try:
        from core.tools.catalog_snapshot import build_action_profiles_for_tool
    except Exception:
        build_action_profiles_for_tool = None
    for item in client.list_tools():
        tool_id = str(item.get("tool_id") or "")
        if not tool_id:
            continue
        if allowed is not None and tool_id not in allowed:
            continue
        if item.get("enabled") is False or item.get("callable_by_llm") is False:
            continue
        if item.get("forbidden") is True:
            continue
        args_schema = item.get("input_schema") or {}
        profiles = action_profiles.get(tool_id, [])
        if not profiles and build_action_profiles_for_tool:
            try:
                profiles = build_action_profiles_for_tool(
                    tool_id,
                    input_schema=args_schema,
                    category=str(item.get("category") or ""),
                    base_permission=str(item.get("permission_action") or "read"),
                )
            except Exception:
                profiles = []
        tools[tool_id] = {
            "description": str(item.get("description") or tool_id),
            "args_schema": args_schema,
            "category": item.get("category") or "",
            "risk_level": item.get("risk_level") or "low",
            "action_profiles": profiles,
            "metadata": item.get("metadata") or {},
        }
    return tools


def _build_runtime_context_budget(registry: dict[str, dict[str, Any]]):
    from agent.llm.config import resolve_provider_config
    from agent.llm.tool_adapter import tool_spec_to_openai_function
    from core.runtime_engine.context_budget import RuntimeContextBudget

    config = dict(resolve_provider_config() or {})
    tool_definitions = [
        tool_spec_to_openai_function({
            "tool_id": tool_id,
            "description": meta.get("description", ""),
            "input_schema": meta.get("args_schema", {}),
            "risk_level": meta.get("risk_level", "low"),
            "action_profiles": meta.get("action_profiles", []),
            "metadata": meta.get("metadata", {}),
        })
        for tool_id, meta in sorted(registry.items())
    ]
    return RuntimeContextBudget.build(
        model=str(config.get("model") or ""),
        tools=tool_definitions,
        context_window_tokens=int(config.get("context_window_tokens") or 0),
        max_input_tokens=int(config.get("max_input_tokens") or 48_000),
        reserved_output_tokens=int(config.get("max_tokens") or 4096),
    )


def _tool_runtime_client():
    from core.tools.integration import get_default_tool_runtime_client
    return get_default_tool_runtime_client()




def _invoke_llm_for_ssot_runtime(**kwargs):
    from agent.llm.runtime import invoke_llm, resolve_invocation_candidates
    from agent.runtime.token_tracker import record_llm_call

    system = str(kwargs.get("system") or "")
    user = str(kwargs.get("user") or "")
    runtime_messages = kwargs.get("messages")
    caller_extra = kwargs.get("extra") or {}
    stream_scope = str(caller_extra.get("stream_scope") or "internal").lower()
    is_planner = stream_scope == "planner"
    # Preserve the exact tool list supplied by QueryLoop. QueryLoop keeps tools
    # visible on response/synthesis turns; an empty list should only appear if a
    # caller intentionally supplied one.
    tools = kwargs.get("tools")
    session_id = str(kwargs.get("session_id") or caller_extra.get("session_id") or "").strip()
    workspace_id = str(kwargs.get("workspace_id") or caller_extra.get("workspace_id") or "").strip()

    extra = {
        "runtime_engine": "ssot_runtime",
        "planner": is_planner,
        "stream_to_user": not is_planner,
        "stream_scope": stream_scope,
    }
    if caller_extra:
        extra.update(caller_extra)

    config_override = {}
    timeout = kwargs.get("timeout")
    if timeout is not None:
        config_override["timeout"] = int(timeout)
    temperature = kwargs.get("temperature")
    if temperature is not None:
        config_override["temperature"] = float(temperature)
    max_tokens = kwargs.get("max_tokens")
    if max_tokens is not None:
        config_override["max_tokens"] = int(max_tokens)

    messages = [
        LLMMessage(
            role=str(message.role),
            content=message.content,
            tool_call_id=message.tool_call_id,
            tool_calls=list(message.tool_calls or []) or None,
        )
        for message in runtime_messages
    ] if isinstance(runtime_messages, list) and all(
        isinstance(message, LLMMessage) for message in runtime_messages
    ) else [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=user),
    ]

    # QueryLoop supplies only pending typed evidence. Original user images are
    # delivered once on the planner call; images produced by tools are delivered
    # once on the next continuation/synthesis call. Encoded bytes stay ephemeral
    # inside this provider request and never enter metadata, history, or traces.
    if caller_extra.get("evidence_parts"):
        try:
            from agent.llm.capabilities import supports_vision
            effective_config = resolve_invocation_candidates(
                "assistant_chat", config_override,
            )[0]
            if supports_vision(effective_config):
                from agent.runtime.vision_inputs import build_vision_content
                from core.runtime_engine.evidence import evidence_to_vision_references
                image_parts: list[dict[str, Any]] = []
                vision_warnings: list[str] = []
                delivered_evidence_ids: list[str] = []
                for evidence_part in caller_extra.get("evidence_parts") or []:
                    references = evidence_to_vision_references([evidence_part])
                    if not references:
                        continue
                    resolved_parts, resolved_warnings = build_vision_content(references, workspace_id)
                    image_parts.extend(resolved_parts)
                    vision_warnings.extend(resolved_warnings)
                    if resolved_parts:
                        delivered_evidence_ids.append(str(evidence_part.get("evidence_id") or ""))
                if image_parts:
                    for index in range(len(messages) - 1, -1, -1):
                        if messages[index].role != "user":
                            continue
                        text_content = messages[index].content
                        if not isinstance(text_content, str):
                            text_content = user
                        messages[index] = LLMMessage(
                            role="user",
                            content=[{"type": "text", "text": text_content}, *image_parts],
                            tool_call_id=messages[index].tool_call_id,
                            tool_calls=messages[index].tool_calls,
                        )
                        break
                    extra["delivered_evidence_ids"] = delivered_evidence_ids
                if vision_warnings:
                    extra["vision_warnings"] = vision_warnings
            else:
                extra["vision_warnings"] = ["当前模型不支持图片识别，图片未发送给模型。"]
        except Exception:
            _LOG.warning("vision attachment preparation failed", exc_info=True)

    resp = invoke_llm(
        task="assistant_chat",
        messages=messages,
        tools=tools,
        user_input=user,
        extra=extra,
        config_override=config_override,
    )
    if extra.get("delivered_evidence_ids"):
        resp.metadata = {
            **(resp.metadata or {}),
            "delivered_evidence_ids": list(extra["delivered_evidence_ids"]),
        }

    # Track token usage
    if workspace_id:
        try:
            usage = resp.usage or {}
            record_llm_call(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                session_id=session_id,
                workspace_id=workspace_id,
                model=resp.model or "",
                provider=resp.provider or "",
            )
        except Exception:
            _LOG.debug("record_llm_call failed", exc_info=True)

    if resp.error:
        # If streaming produced partial content before error, return it
        # instead of failing entirely (common with timeout on slow providers).
        # v4.1: accept ANY non-empty content — even a single character is
        # better than a generic fallback.
        if resp.content and resp.content.strip():
            return resp
        raise RuntimeError(resp.error)
    # Preserve finish_reason, usage, and truncation metadata. QueryLoop accepts
    # only provider-native tool calls; plain JSON remains ordinary assistant text.
    return resp


def _run_async(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    box: dict[str, Any] = {}

    def _target():
        try:
            box["result"] = asyncio.run(awaitable)
        except Exception as exc:  # pragma: no cover - defensive branch
            box["error"] = exc

    import threading

    from storage.principal import bind_storage_principal
    thread = threading.Thread(target=bind_storage_principal(_target), daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


_BOGUS_FINAL_PATTERNS = (
    "已完成。",
    "工具执行成功",
    "工具执行完成",
    "No tools were executed",
    "readartifact completed",
    "readartifact succeeded",
)


def _is_bogus_final(text: str) -> bool:
    """Return True when *text* is a placeholder stub rather than
    a real answer produced by the QueryLoop response state."""
    t = text.strip()
    if len(t) <= 1:
        return True
    return any(p in t for p in _BOGUS_FINAL_PATTERNS)


def _final_response(runtime_result) -> str:
    text = str(getattr(runtime_result, "final_response", "") or "").strip()

    if text:
        from agent.llm.runtime import sanitize_provider_output
        text, _ = sanitize_provider_output(text)
        text = text.strip()

    # If the final response is a known placeholder, return empty so the caller
    # can surface a tool-result-based fallback instead of a useless stub.
    if text and _is_bogus_final(text):
        text = ""

    if text:
        return text
    # No tool results and no text — return empty so caller can fall back.
    return ""


def _tool_result_fallback_from_projected_calls(tool_calls: list[dict[str, Any]]) -> str:
    """Build a useful user-facing fallback from projected tool-call results."""
    if not tool_calls:
        return ""

    ok_count = sum(1 for call in tool_calls if call.get("ok"))
    fail_count = len(tool_calls) - ok_count
    lines = [
        f"工具结果已返回：成功 {ok_count} 个"
        + (f"，失败 {fail_count} 个" if fail_count else "")
    ]

    if len(tool_calls) > 10:
        lines.append(f"以下仅展示前 10 条，共 {len(tool_calls)} 条。")

    for call in tool_calls[:10]:
        tool_id = str(call.get("tool_id") or "tool")
        status = "✅" if call.get("ok") else "❌"
        summary = str(call.get("summary") or "").strip()
        result = call.get("result") if isinstance(call.get("result"), dict) else {}
        lines.append(f"\n### {status} {tool_id}")
        if summary and summary not in {"Tool completed", "Tool failed"}:
            lines.append(summary[:1200] + ("..." if len(summary) > 1200 else ""))
        command = result.get("command") or result.get("description")
        if command:
            lines.append(f"> `{str(command)[:160]}`")
        if result.get("exit_code") is not None:
            lines.append(f"Exit: exit_code={result.get('exit_code')}")
        stdout = str(result.get("stdout") or "").strip()
        stderr = str(result.get("stderr") or "").strip()
        if stdout:
            lines.append(f"```\n{stdout[:1200]}\n```")
        if stderr:
            lines.append(f"```\n{stderr[:1200]}\n```")
        artifacts = call.get("artifacts") if isinstance(call.get("artifacts"), list) else []
        if artifacts:
            ids = [
                str(item.get("artifact_id") or item.get("title") or "").strip()
                for item in artifacts[:5]
                if isinstance(item, dict)
            ]
            ids = [value for value in ids if value]
            if ids:
                lines.append("产物：" + "、".join(ids))

    if len(tool_calls) > 10:
        lines.append(f"\n其余 {len(tool_calls) - 10} 个工具结果已省略。")

    return "\n".join(lines).strip()


def _project_tool_calls(runtime_result) -> list[dict[str, Any]]:
    calls = []
    for node_id, tr in (runtime_result.node_results or {}).items():
        data = tr.data if isinstance(tr.data, dict) else {"value": tr.data}
        raw_ids = list(data.get("artifact_ids") or [])
        # Normalise artifacts: frontend expects objects, not plain strings.
        artifacts: list[dict[str, str]] = []
        for aid in raw_ids:
            if isinstance(aid, dict):
                artifacts.append({
                    "artifact_id": str(aid.get("artifact_id", aid.get("id", ""))),
                    "artifact_type": str(aid.get("artifact_type", aid.get("type", ""))),
                    "title": str(aid.get("title", aid.get("name", ""))),
                })
            elif isinstance(aid, str):
                artifacts.append({
                    "artifact_id": aid,
                    "artifact_type": "",
                    "title": aid,
                })

        calls.append({
            "call_id": node_id,
            "tool_id": tr.tool,
            "ok": bool(tr.success),
            "status": "succeeded" if tr.success else "failed",
            "summary": _tool_summary(data, tr),
            "result": data.get("output", data),
            "duration_ms": tr.latency_ms,
            "errors": list(data.get("errors") or ([tr.error] if tr.error else [])),
            "warnings": list(data.get("warnings") or []),
            "artifacts": artifacts,
            "metadata": {
                "runtime_engine": "ssot_runtime",
                "node_id": node_id,
                "duration_ms": tr.latency_ms,
                "redacted": bool(data.get("redacted", True)),
                "orchestration": dict(data.get("_orchestration") or {}),
            },
        })
    return calls


def _tool_summary(data: dict[str, Any], tr) -> str:
    for key in ("summary", "message", "error"):
        value = data.get(key)
        if value:
            return str(value)[:500]
    if tr.error:
        return str(tr.error)[:500]
    return "Tool completed" if tr.success else "Tool failed"


def _project_events(runtime_result, trace_id: str, turn_id: str) -> list[dict[str, Any]]:
    events = []
    for batch_index, batch in enumerate((runtime_result.metadata or {}).get("orchestration_batches") or []):
        if not isinstance(batch, dict):
            continue
        parallel_steps_by_layer = list(batch.get("parallel_steps") or [])
        for layer_index, steps in enumerate(batch.get("layers") or [], start=1):
            parallel_steps = (
                parallel_steps_by_layer[layer_index - 1]
                if layer_index <= len(parallel_steps_by_layer)
                and isinstance(parallel_steps_by_layer[layer_index - 1], list)
                else []
            )
            events.append({
                "event_id": f"orchestration-{turn_id}-{batch_index}-{layer_index}",
                "event_type": "orchestration_layer_completed",
                "type": "orchestration_layer_completed",
                "name": "协同步骤执行完成",
                "trace_id": trace_id,
                "run_id": turn_id,
                "timestamp": time.time(),
                "status": "completed",
                "summary": f"第 {layer_index} 组：{len(list(steps or []))} 个步骤",
                "metadata": {
                    "batch": batch_index + 1,
                    "layer": layer_index,
                    "steps": list(steps or []),
                    "parallel": len(parallel_steps) > 1,
                    "parallel_steps": list(parallel_steps),
                },
            })
    for node_id, tr in (runtime_result.node_results or {}).items():
        events.append({
            "event_id": f"tool-start-{turn_id}-{node_id}",
            "event_type": "tool_call",
            "type": "tool_call",
            "name": "tool_call",
            "tool_id": tr.tool,
            "node_id": node_id,
            "call_id": node_id,
            "trace_id": trace_id,
            "run_id": turn_id,
            "timestamp": time.time(),
            "status": "started",
        })
        events.append({
            "event_id": f"tool-result-{turn_id}-{node_id}",
            "event_type": "tool_result",
            "type": "tool_result",
            "name": "tool_result",
            "tool_id": tr.tool,
            "node_id": node_id,
            "call_id": node_id,
            "trace_id": trace_id,
            "run_id": turn_id,
            "timestamp": time.time(),
            "status": "success" if tr.success else "failed",
            "ok": bool(tr.success),
            "summary": _tool_summary(tr.data if isinstance(tr.data, dict) else {}, tr),
            "duration_ms": tr.latency_ms,
            "metadata": {"orchestration": dict(
                (tr.data if isinstance(tr.data, dict) else {}).get("_orchestration") or {}
            )},
        })
    for idx, ev in enumerate((runtime_result.metadata or {}).get("retry_events") or []):
        if not isinstance(ev, dict):
            continue
        events.append({
            "event_id": f"retry-{turn_id}-{idx}",
            "event_type": "tool_retry",
            "type": "tool_retry",
            "name": "工具自动重试",
            "status": ev.get("final_status") or ("succeeded" if ev.get("retry_allowed") else "blocked"),
            "summary": _retry_event_summary(ev),
            "tool_id": ev.get("tool_id", ""),
            "node_id": ev.get("node_id", ""),
            "trace_id": trace_id,
            "run_id": turn_id,
            "timestamp": time.time(),
            "duration_ms": ev.get("duration_ms", 0),
            "metadata": ev,
        })
    return events


def _retry_event_summary(ev: dict[str, Any]) -> str:
    tool_id = str(ev.get("tool_id") or ev.get("node_id") or "tool")
    reason = str(ev.get("reason") or ev.get("error_code") or "")
    if ev.get("retry_allowed"):
        if str(ev.get("final_status") or "") == "succeeded":
            return f"{tool_id} 首次失败后已自动重试并恢复"
        return f"{tool_id} 已按策略重试，但仍未完成"
    if ev.get("blocked_by_policy"):
        if reason == "non_idempotent" or "side_effect_not_retryable" in reason or reason == "execute_command_not_retryable":
            return f"{tool_id} 未原样重放，以避免重复副作用；模型可改用其他策略"
        return f"{tool_id} 未自动重试：{reason or '策略禁止重试'}"
    return f"{tool_id} 未触发重试：{reason or '不满足重试条件'}"


def _event(event_type: str, name: str, trace_id: str, turn_id: str, *, started_at: float) -> dict[str, Any]:
    return {
        "type": event_type,
        "name": name,
        "trace_id": trace_id,
        "run_id": turn_id,
        "timestamp": time.time(),
        "duration_ms": int((time.monotonic() - started_at) * 1000),
    }


def _timeline_summary(*, started: float, events: list, tool_calls: list, runtime_result) -> dict[str, Any]:
    return {
        "node_count": max(len(events), 1),
        "total_duration_ms": int((time.monotonic() - started) * 1000),
        "artifact_saved_count": sum(len(c.get("artifacts") or []) for c in tool_calls),
        "execution_duration_ms": int(getattr(runtime_result, "execution_latency_ms", 0) or 0),
        "llm_calls": int((runtime_result.metadata or {}).get("llm_calls", 0) or 0),
        "tool_calls": len(tool_calls),
        "max_parallel_width": int((runtime_result.metadata or {}).get("metrics", {}).get("max_parallel_width", 0) or 0),
    }


def _tool_decision(runtime_result, tool_calls: list) -> dict[str, Any]:
    if not tool_calls:
        return {"needed": False, "reason": "SSOT Runtime planner selected no tools.", "selected_tools": []}
    return {
        "needed": True,
        "reason": "SSOT Runtime execution graph selected tool nodes.",
        "selected_tools": [c["tool_id"] for c in tool_calls],
        "tool_count": len(tool_calls),
    }


def _current_provider_name() -> str:
    try:
        from agent.llm.config import resolve_provider_config
        return str(resolve_provider_config().get("provider") or "")
    except Exception:
        return ""


def _current_model_name() -> str:
    try:
        from agent.llm.config import resolve_provider_config
        return str(resolve_provider_config().get("model") or "")
    except Exception:
        return ""


# ── Conversation history block builder ──────────────────────────────

_HISTORY_RECENT_MESSAGES = 30
_HISTORY_REFERENCE_PATTERNS = (
    "前面", "之前", "上次", "刚才", "还记得", "记得",
    "上一轮", "前一轮", "前面的", "之前的", "刚才的",
)
_HISTORY_IMMEDIATE_PATTERNS = (
    "继续", "接着", "详细点", "再详细", "展开", "再说说", "这个", "那个", "然后呢",
)
_HISTORY_STOP_TERMS = frozenset({
    "一下", "一个", "这个", "那个", "什么", "怎么", "如何", "帮我", "看看",
    "查看", "进行", "需要", "可以", "现在", "目前", "问题", "结果", "分析",
})
def _build_retrieved_context_block(
    *, workspace_id: str, session_id: str, task_id: str, user_input: str,
    max_tokens: int = 3000,
    include_workspace_memory: bool = True,
) -> str:
    """Retrieve governed context without silently widening child-agent access.

    A subagent has a restricted tool profile and an isolated child session.
    Its automatic context must not reintroduce workspace/global memory that the
    profile did not expose.  Workspace knowledge remains available when its
    canonical tool profile permits research-oriented work.
    """
    if not workspace_id or not user_input.strip():
        return ""
    try:
        from core.context.unified_retriever import get_retriever
        from storage.memory_governance import MemoryStore

        retriever = get_retriever(workspace_id)
        if include_workspace_memory:
            retrieved = retriever.retrieve_for_context(
                user_input,
                top_k_memory=3,
                top_k_knowledge=2,
                session_id=session_id,
                task_id=task_id,
            )
        else:
            retrieved = {
                "memory_hits": [],
                "knowledge_hits": retriever.search_knowledge(user_input, top_k=2),
            }
        from core.runtime_engine.context_budget import truncate_text_to_tokens
        from storage.redaction import redact_text

        lines: list[str] = []
        item_tokens = max(200, min(750, max_tokens // 3))
        if include_workspace_memory:
            core_rules = MemoryStore().list_retrievable(
                workspace_id,
                memory_type="core_rule",
                limit=8,
            )
            for rule in core_rules:
                content = redact_text(str(rule.get("content") or rule.get("summary") or "")).strip()
                if content:
                    compacted, _ = truncate_text_to_tokens(content, min(item_tokens, 350))
                    lines.append(f"[core-rule scope=workspace authority=explicit-user] {compacted}")
        for hit in retrieved.get("memory_hits", [])[:3]:
            if str(hit.get("memory_type") or "") == "core_rule":
                continue
            content = redact_text(str(hit.get("content") or hit.get("summary") or "")).strip()
            if content:
                compacted, _ = truncate_text_to_tokens(content, item_tokens)
                scope = str(hit.get("scope") or "workspace")
                lines.append(f"[memory scope={scope}] {compacted}")
        for hit in retrieved.get("knowledge_hits", [])[:2]:
            content = redact_text(str(hit.get("content") or hit.get("summary") or "")).strip()
            if content:
                compacted, _ = truncate_text_to_tokens(content, item_tokens)
                lines.append(f"[knowledge scope=workspace] {compacted}")
        compacted, _ = truncate_text_to_tokens("\n".join(lines), max_tokens)
        return compacted
    except Exception:
        _LOG.debug("governed context retrieval failed", exc_info=True)
        return ""


def _build_history_block(
    session,
    *,
    user_input: str = "",
    max_tokens: int = 8000,
    exclude_run_id: str = "",
    exclude_client_request_id: str = "",
) -> str:
    """Build prompt-ready conversation context from the session message SSOT.

    Source order:
      1. ``SessionMessageStore`` full persisted messages
      2. in-memory ``session.history`` entries not yet flushed

    The block keeps recent messages verbatim, summarizes older turns, and
    pulls a small retrieved-history section when the current input references
    earlier conversation. This preserves long-session entities without reviving
    a second runtime path.
    """
    try:
        messages = _load_context_messages(
            session,
            exclude_run_id=exclude_run_id,
            exclude_client_request_id=exclude_client_request_id,
        )
        if not messages:
            return ""

        from core.runtime_engine.context_budget import estimate_text_tokens, truncate_text_to_tokens

        recent, older, include_retrieved = _select_history_messages(messages, user_input)
        if not recent and not older:
            return ""
        parts: list[str] = []
        recent_budget = max(800, int(max_tokens * 0.65))
        summary_budget = max(300, int(max_tokens * 0.22))
        reference_budget = max(200, max_tokens - recent_budget - summary_budget)
        per_message_tokens = max(100, min(600, max_tokens // 10))
        recent_text = _format_recent_history(
            recent,
            max_tokens=recent_budget,
            per_message_tokens=per_message_tokens,
        )
        if older:
            summary = _summarize_older_messages(older, max_tokens=summary_budget)
            if summary:
                parts.append("SESSION SUMMARY:\n" + summary)
        retrieved = _retrieve_history_references(messages, user_input) if include_retrieved else []
        if retrieved:
            retrieved_lines = []
            for message in retrieved:
                content, _ = truncate_text_to_tokens(message["content"], per_message_tokens)
                retrieved_lines.append(f"  [{message['role']}] {content}")
            retrieved_text, _ = truncate_text_to_tokens("\n".join(retrieved_lines), reference_budget)
            parts.append("RETRIEVED HISTORY:\n" + retrieved_text)
        if recent_text:
            parts.append("RECENT CONVERSATION HISTORY:\n" + recent_text)
        block = "\n\n".join(parts)
        if estimate_text_tokens(block) <= max_tokens:
            return block
        # Never head-truncate a long block: that discards the newest turns.
        fallback = "RECENT CONVERSATION HISTORY:\n" + _format_recent_history(
            recent,
            max_tokens=max(100, max_tokens - 20),
            per_message_tokens=per_message_tokens,
        )
        fallback, _ = truncate_text_to_tokens(fallback, max_tokens)
        return fallback
    except Exception:
        _LOG.debug("conversation history block build failed", exc_info=True)
        return ""


def _select_history_messages(
    messages: list[dict[str, str]],
    user_input: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]], bool]:
    """Select only history that can help the current turn.

    Short deictic follow-ups need the immediately preceding exchange, while an
    explicit long-range reference can use the bounded historical summary.  A
    standalone topic receives only lexically related prior messages; unrelated
    turns are omitted instead of consuming context merely because they share a
    session.
    """
    if not messages:
        return [], [], False
    text = str(user_input or "").strip()
    if _is_immediate_followup(text):
        return messages[-2:], [], False
    if any(pattern in text for pattern in _HISTORY_REFERENCE_PATTERNS):
        return (
            messages[-min(8, _HISTORY_RECENT_MESSAGES):],
            messages[:-min(8, _HISTORY_RECENT_MESSAGES)],
            True,
        )

    query_terms = _history_terms(text)
    if not query_terms:
        return [], [], False
    recent_pool = messages[-_HISTORY_RECENT_MESSAGES:]
    matched_indexes = {
        index
        for index, message in enumerate(recent_pool)
        if _message_matches_history_terms(message.get("content", ""), query_terms)
    }
    if not matched_indexes:
        return [], [], False

    # Keep the adjacent half of a matched user/assistant exchange so evidence
    # and its response are not separated.
    selected_indexes = set(matched_indexes)
    for index in tuple(matched_indexes):
        role = str(recent_pool[index].get("role") or "")
        if role == "assistant" and index > 0:
            selected_indexes.add(index - 1)
        elif role == "user" and index + 1 < len(recent_pool):
            selected_indexes.add(index + 1)
    selected = [recent_pool[index] for index in sorted(selected_indexes)][-8:]
    return selected, [], False


def _is_immediate_followup(text: str) -> bool:
    import re

    value = str(text or "").strip()
    if not value or len(value) > 80:
        return False
    if re.fullmatch(
        r"(?:全部|所有|全都|都要|这些|以上|它们|每个|每一个)[。.!！?？\s]*",
        value,
    ):
        return True
    # Quantity-only continuations such as “再来30条” have no lexical topic
    # signal, but their only coherent referent is the immediately preceding
    # exchange. Treat them as continuation instructions before lexical history
    # selection, so the original target, quantity and output constraints remain
    # visible to the canonical QueryLoop prompt.
    if re.fullmatch(
        r"(?:再来|再给|再生成|再写|再列|再补)\s*(?:\d+|几|一些|一批)?\s*(?:条|个|项|份|段|组)?[。.!！?？\s]*",
        value,
    ):
        return True
    return any(value.startswith(pattern) for pattern in _HISTORY_IMMEDIATE_PATTERNS)


def _history_terms(text: str) -> set[str]:
    import re

    value = str(text or "").lower()
    terms = {
        token for token in re.findall(r"[a-z0-9][a-z0-9_.:/-]{2,}", value)
        if token not in _HISTORY_STOP_TERMS
    }
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        if sequence not in _HISTORY_STOP_TERMS:
            terms.add(sequence)
        for size in (2, 3, 4):
            for index in range(max(0, len(sequence) - size + 1)):
                token = sequence[index:index + size]
                if token not in _HISTORY_STOP_TERMS:
                    terms.add(token)
    return terms


def _message_matches_history_terms(text: str, terms: set[str]) -> bool:
    value = str(text or "").lower()
    return any(term in value for term in terms)


def _attachment_reference_terms(text: str) -> set[str]:
    value = str(text or "").lower()
    terms = (
        "附件", "文件", "文档", "图片", "照片", "截图", "配置", "表格",
        "pdf", "docx", "word", "xlsx", "excel", "ppt", "日志",
    )
    return {term for term in terms if term in value}


def _recent_session_attachments(
    session,
    *,
    user_input: str = "",
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return recent user attachment references for a same-session follow-up.

    Only FileStore metadata already persisted with a user message is reused.
    This is not a filesystem lookup and never revives files from another
    workspace or session.
    """
    workspace_id = str(getattr(session, "workspace_id", "") or "")
    session_id = str(getattr(session, "session_id", "") or "")
    if not workspace_id or not session_id:
        return []
    try:
        from storage.message_store import SessionMessageStore

        messages = SessionMessageStore(session_id=session_id, ws_id=workspace_id).get_messages()
        attachment_message_index = -1
        items: list[dict[str, Any]] = []
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if str(message.get("role") or "") != "user":
                continue
            raw = (message.get("metadata") or {}).get("attachments") or []
            if isinstance(raw, list):
                items = [item for item in raw if isinstance(item, dict)][:limit]
            if items:
                attachment_message_index = index
                break
        if not items:
            return []

        explicit_reference = bool(_attachment_reference_terms(user_input))
        turns_after_attachment = len(messages) - attachment_message_index - 1
        # Implicit reuse is intentionally limited to the immediate follow-up.
        # Older managed files stay available in FileStore but are not injected
        # into unrelated topics later in the same session.
        if not explicit_reference and turns_after_attachment > 1:
            return []
        return items
    except Exception:
        _LOG.debug("recent attachment lookup failed", exc_info=True)
        return []


def _merge_attachment_references(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate validated attachment metadata while retaining caller order."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            file_id = str(item.get("file_id") or "").strip()
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            merged.append(dict(item))
            if len(merged) >= 8:
                return merged
    return merged


def _active_attachment_references(
    workspace_id: str,
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only active, current-workspace FileStore records for reuse."""
    if not workspace_id:
        return []
    try:
        from backend.core.chat_attachments import normalize_chat_attachments

        active: list[dict[str, Any]] = []
        for attachment in attachments:
            try:
                active.extend(normalize_chat_attachments(workspace_id, [attachment]))
            except ValueError:
                # A historic message may reference a file the user removed.
                # It must never become a stale trusted handle in a later turn.
                continue
        return active
    except Exception:
        _LOG.debug("attachment revalidation failed", exc_info=True)
        return []


def _load_context_messages(
    session,
    *,
    exclude_run_id: str = "",
    exclude_client_request_id: str = "",
) -> list[dict[str, str]]:
    persisted: list[dict[str, str]] = []
    persisted_seen: set[str] = set()
    ws_id = str(getattr(session, "workspace_id", "") or "")
    session_id = str(getattr(session, "session_id", "") or "")
    if ws_id and session_id:
        try:
            from storage.message_store import SessionMessageStore

            for m in SessionMessageStore(session_id=session_id, ws_id=ws_id).get_messages():
                metadata = m.get("metadata") if isinstance(m.get("metadata"), dict) else {}
                if (
                    exclude_client_request_id
                    and str(metadata.get("client_request_id") or "") == exclude_client_request_id
                ):
                    continue
                _append_context_message(persisted, persisted_seen, m)
        except Exception:
            _LOG.debug("SessionMessageStore history read failed for %s", session_id, exc_info=True)

    memory: list[dict[str, str]] = []
    memory_seen: set[str] = set()
    for i, msg in enumerate(list(getattr(session, "history", None) or [])):
        role = str(getattr(msg, "role", "") or "")
        content = str(getattr(msg, "content", "") or "")
        client_request_id = str(getattr(msg, "client_request_id", "") or "")
        if exclude_client_request_id and client_request_id == exclude_client_request_id:
            continue
        _append_context_message(memory, memory_seen, {
            "message_id": getattr(msg, "id", "") or getattr(msg, "message_id", "") or f"mem:{i}:{role}:{content[:40]}",
            "run_id": getattr(msg, "run_id", "") or "",
            "role": role,
            "content": content,
            "metadata": {"client_request_id": client_request_id},
        })
    overlap = _history_overlap(persisted, memory)
    merged = persisted + memory[overlap:]
    excluded = str(exclude_run_id or "").strip()
    if excluded:
        merged = [
            message for message in merged
            if not str(message.get("message_id") or "").startswith(f"{excluded}:")
        ]
    return merged


def _history_overlap(
    persisted: list[dict[str, str]], memory: list[dict[str, str]],
) -> int:
    """Return the longest persisted suffix duplicated at memory's prefix."""
    for size in range(min(len(persisted), len(memory)), 0, -1):
        if all(
            left.get("role") == right.get("role")
            and left.get("content") == right.get("content")
            for left, right in zip(persisted[-size:], memory[:size])
        ):
            return size
    return 0


def _format_recent_history(
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    per_message_tokens: int,
) -> str:
    """Fit newest messages into a budget while preserving chronological order."""
    from core.runtime_engine.context_budget import estimate_text_tokens, truncate_text_to_tokens

    selected: list[str] = []
    used = 0
    for message in reversed(messages):
        content, _ = truncate_text_to_tokens(message["content"], per_message_tokens)
        line = (
            f"  [{message['role']}] "
            f"{content}"
        )
        cost = estimate_text_tokens(line) + 1
        if selected and used + cost > max_tokens:
            break
        if not selected and cost > max_tokens:
            line, _ = truncate_text_to_tokens(line, max_tokens)
            cost = estimate_text_tokens(line)
        selected.append(line)
        used += cost
    return "\n".join(reversed(selected))


def _append_context_message(messages: list[dict[str, Any]], seen: set[str], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    role = str(raw.get("role") or "")
    content = str(raw.get("content") or "").strip()
    if role not in ("user", "assistant") or not content:
        return
    key = str(raw.get("message_id") or raw.get("id") or raw.get("run_id") or f"{role}:{content[:80]}")
    if key in seen:
        return
    seen.add(key)
    # Persisted assistant messages can carry a compact, redacted execution
    # breadcrumb. Keep it with the assistant turn; do not recreate protocol
    # tool messages or inject raw tool output into later model context.
    metadata = raw.get("metadata") or {}
    tool_context = (metadata.get("tool_context") or [])
    if role == "assistant" and isinstance(tool_context, list):
        facts = []
        for item in tool_context[:8]:
            if not isinstance(item, dict):
                continue
            tool_id = str(item.get("tool_id") or "tool")[:120]
            status = "succeeded" if item.get("ok") else "failed"
            summary = str(item.get("summary") or "").strip().replace("\n", " ")[:300]
            errors = "; ".join(str(error)[:160] for error in list(item.get("errors") or [])[:2])
            detail = summary or errors
            facts.append(f"- {tool_id}: {status}" + (f" — {detail}" if detail else ""))
        if facts:
            content += "\n\n[Tool execution summary]\n" + "\n".join(facts)
    message: dict[str, Any] = {
        "message_id": key,
        "role": role,
        "content": content,
    }
    history_state = metadata.get("history_state")
    run_id = str(raw.get("run_id") or metadata.get("run_id") or "").strip()
    if run_id:
        message["run_id"] = run_id
    if isinstance(history_state, dict) and history_state.get("schema") == "runtime.history_state.v1":
        from storage.redaction import redact_value

        message["history_state"] = redact_value(history_state)
    messages.append(message)


def _summarize_older_messages(
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
) -> str:
    from core.runtime_engine.context_budget import estimate_text_tokens, truncate_text_to_tokens
    from core.runtime_engine.context_compaction import history_state_signals
    from storage.redaction import redact_text

    # Select on explicit state signals rather than one flat keyword gate.
    # Stable ordering is restored after scoring so cause/effect remains legible.
    ranked = sorted(
        enumerate(messages),
        key=lambda item: (-_history_record_score(item[1]), -item[0]),
    )
    selected = sorted(
        [item for item in ranked if _history_record_score(item[1]) > 0][:12],
        key=lambda item: item[0],
    )
    if not selected and messages:
        indexes = sorted(set(range(min(2, len(messages)))) | set(range(max(0, len(messages) - 3), len(messages))))
        selected = [(index, messages[index]) for index in indexes]

    lines: list[str] = []
    for _, message in selected:
        content = redact_text(message["content"])
        compacted, _ = truncate_text_to_tokens(content, min(220, max_tokens))
        state = message.get("history_state") if isinstance(message.get("history_state"), dict) else {}
        signals = ",".join(str(value) for value in state.get("signals", []) if value) or ",".join(history_state_signals(content)) or "context"
        state_projection = {
            key: state[key]
            for key in ("entities", "constraints", "tool_facts", "unresolved", "references")
            if state.get(key)
        }
        state_text = (
            " state=" + json.dumps(state_projection, ensure_ascii=False, separators=(",", ":"), default=str)
            if state_projection else ""
        )
        lines.append(f"  [history_state role={message['role']} signals={signals}]{state_text} excerpt={compacted}")
        if estimate_text_tokens("\n".join(lines)) >= max_tokens:
            break
    if not lines:
        return ""
    compacted, _ = truncate_text_to_tokens("\n".join(lines), max_tokens)
    return compacted


def _retrieve_history_references(messages: list[dict[str, str]], user_input: str) -> list[dict[str, str]]:
    """Return bounded, query-relevant history outside the recent window.

    Explicit references must recover matching middle turns in a long session.
    Query matches take precedence over generic importance so unrelated later
    constraints cannot crowd out the named historical fact.
    """
    text = (user_input or "").strip()
    if not text or not any(p in text for p in _HISTORY_REFERENCE_PATTERNS):
        return []
    recent_count = min(8, _HISTORY_RECENT_MESSAGES)
    candidates = messages[:-recent_count] if len(messages) > recent_count else []
    terms = _history_terms(text)
    matched_indexes = [
        index for index, message in enumerate(candidates)
        if terms and _message_matches_history_terms(message.get("content", ""), terms)
    ]
    if matched_indexes:
        selected_indexes = set(matched_indexes[-8:])
        for index in range(len(candidates) - 1, -1, -1):
            if len(selected_indexes) >= 8:
                break
            if index not in selected_indexes and _history_record_score(candidates[index]) > 0:
                selected_indexes.add(index)
        return [candidates[index] for index in sorted(selected_indexes)]
    important_indexes = [
        index for index, message in enumerate(candidates)
        if _history_record_score(message) > 0
    ]
    return [candidates[index] for index in important_indexes[-8:]]

def _history_record_score(message: dict[str, Any]) -> int:
    from core.runtime_engine.context_compaction import history_importance_score

    state = message.get("history_state") if isinstance(message.get("history_state"), dict) else {}
    state_weights = {
        "constraint": 5, "correction": 5, "decision": 4,
        "status": 3, "entity": 2, "artifact": 1,
    }
    persisted_score = sum(
        state_weights.get(str(signal), 0)
        for signal in state.get("signals", [])
    )
    unresolved_score = 6 if any(
        isinstance(item, dict) and not bool(item.get("ok", False))
        for item in list(state.get("unresolved") or [])
    ) else 0
    return max(
        persisted_score,
        unresolved_score,
        history_importance_score(str(message.get("content") or "")),
    )


# ── Session history sync ──────────────────────────────────────

def _sync_session_history(
    session,
    user_input: str,
    final_response: str,
    *,
    include_user: bool = True,
    include_assistant: bool = True,
    run_id: str = "",
    client_request_id: str = "",
) -> None:
    """Append current turn to session.history for context in next turns."""
    try:
        from agent.protocol.message import UserMessage, AssistantMessage

        history = getattr(session, "history", None)
        if history is None:
            history = []
            session.history = history

        # Dedup check: skip if last entries already match
        if include_user and not include_assistant and history:
            last = history[-1]
            if getattr(last, "role", "") == "user" and getattr(last, "content", "") == user_input:
                return
        if include_assistant and not include_user and history:
            last = history[-1]
            if getattr(last, "role", "") == "assistant" and getattr(last, "content", "") == final_response:
                return
        if len(history) >= 2:
            last_user = history[-2]
            last_asst = history[-1]
            if (getattr(last_user, "role", "") == "user"
                and getattr(last_asst, "role", "") == "assistant"
                and getattr(last_user, "content", "") == user_input
                and getattr(last_asst, "content", "") == final_response):
                return

        if not final_response and not (include_user and user_input):
            return

        if include_user and user_input:
            history.append(UserMessage(
                content=user_input,
                message_id=(f"{client_request_id}:user" if client_request_id else f"{run_id}:user"),
                run_id=run_id,
                client_request_id=client_request_id,
            ))
        if include_assistant and final_response:
            history.append(AssistantMessage(
                content=final_response,
                message_id=f"{run_id}:assistant",
                run_id=run_id,
                client_request_id=client_request_id,
            ))
    except Exception:
        logging.getLogger(__name__).warning("Failed to sync session history", exc_info=True)
def _approved_call_key(tool_id: str, args: dict[str, Any]) -> str:
    """Stable private key for an exact server-approved tool call."""
    import json

    return json.dumps(
        {"tool_id": str(tool_id), "arguments": dict(args or {})},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


def _approved_call_grants(grant) -> dict[str, list[str]]:
    """Build single-use approval queues from a typed server grant only."""
    from core.runtime_engine.models import ApprovedToolContinuation

    if not isinstance(grant, ApprovedToolContinuation):
        return {}
    node_ids = tuple(str(value) for value in grant.approved_node_ids)
    approval_ids = tuple(str(value) for value in grant.approval_ids)
    if not node_ids or not approval_ids:
        return {}
    # A persisted approval is never a bearer capability for a batch.  Only
    # a one-to-one node/id mapping may produce a canonical execution grant.
    if len(approval_ids) != len(node_ids):
        return {}
    node_approval_ids = dict(zip(node_ids, approval_ids))
    grants: dict[str, list[str]] = {}
    for call in grant.tool_calls:
        if not isinstance(call, dict):
            continue
        approval_id = node_approval_ids.get(str(call.get("id") or ""))
        call_tool_id = str(call.get("name") or "").replace("__", ".")
        call_args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        if approval_id and call_tool_id:
            grants.setdefault(_approved_call_key(call_tool_id, call_args), []).append(approval_id)
    return grants


def _make_tool_handler(
    *,
    client,
    tool_id: str,
    workspace_id: str,
    session_id: str,
    run_id: str,
    approval_run_id: str = "",
    trace_id: str,
    requested_by: str,
    approved_call_grants: dict[str, list[str]] | None = None,
):
    single_use_grants = approved_call_grants if approved_call_grants is not None else {}

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        from core.tools.context import ToolRuntimeContext, get_runtime_cancel_check

        args = client.canonicalize_arguments(tool_id, dict(args or {}))
        grant_key = _approved_call_key(tool_id, args)
        approval_queue = single_use_grants.get(grant_key) or []
        approval_id = approval_queue.pop(0) if approval_queue else None
        if not approval_queue:
            single_use_grants.pop(grant_key, None)
        ctx = ToolRuntimeContext(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            approval_run_id=approval_run_id or None,
            trace_id=trace_id,
            requested_by=requested_by,
            module="ssot_runtime",
            approval_id=approval_id,
            cancel_check=get_runtime_cancel_check(),
        )
        result = await asyncio.to_thread(client.invoke, tool_id, args, context=ctx)
        payload = dict(result.output or {})
        payload.setdefault("status", result.status)
        payload.setdefault("ok", result.status in ("succeeded", "dry_run"))
        payload.setdefault("summary", result.summary or "")
        payload.setdefault("artifact_ids", list(result.artifact_ids or []))
        payload.setdefault("warnings", list(result.warnings or []))
        payload.setdefault("errors", list(result.errors or []))
        payload.setdefault("duration_ms", result.duration_ms)
        payload.setdefault("redacted", bool(result.redacted))
        return payload

    return _handler
