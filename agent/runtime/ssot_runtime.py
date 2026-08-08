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
_APPROVAL_WAIT_SECONDS = 65
_MEMORY_WRITE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="ssot-memory-write",
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
    metadata_in = dict(getattr(turn.op, "metadata", {}) or {})

    # Build the full LLM-visible tool registry first. RuntimeContextBudget
    # deducts its schema cost before assigning history/retrieval capacity.
    ssot_registry = _build_ssot_runtime_tool_registry(allowed_tool_ids)
    runtime_context_budget = _build_runtime_context_budget(ssot_registry)

    # ── Build canonical conversation context for prompt injection ──
    metadata_in["__raw_user_input"] = user_input
    history_block = _build_history_block(
        session,
        user_input=user_input,
        max_tokens=runtime_context_budget.history_tokens,
    )
    if history_block:
        metadata_in["conversation_history_block"] = history_block
    retrieved_context_block = _build_retrieved_context_block(
        workspace_id=workspace_id,
        session_id=session_id,
        task_id=turn.turn_id,
        user_input=user_input,
        max_tokens=runtime_context_budget.retrieved_context_tokens,
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
            context_budget=runtime_context_budget,
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
                ok_count = sum(1 for c in tool_calls if c.get('ok'))
                final_response = f"服务已完成。共调用 {ok_count} 个工具。"
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
        }
        runtime_errors = list(runtime_result.errors or [])
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

    except Exception as exc:
        _LOG.exception("SSOT Runtime turn failed")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        events.append(_event("error", "SSOT Runtime error", trace_id, turn.turn_id, started_at=started))
        result = AgentResult(
            ok=False,
            final_response=f"SSOT Runtime failed: {str(exc)[:300]}",
            events=events,
            trace_id=trace_id,
            session_id=session_id,
            turn_id=turn.turn_id,
            errors=[str(exc)[:500]],
            metadata={
                **context.metadata,
                "runtime_engine": "ssot_runtime",
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
    _sync_session_history(session, user_input, result.final_response)

    persist_run_record(session, turn, result, context)

    # ── Experience journal and memory reflection ─────────────────────
    # Every completed turn is durable experience. Explicit user memory
    # commands are applied immediately; ordinary turns are consolidated only
    # at an operational task boundary or after a small accumulated batch.
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
    context_budget=None,
):
    from core.runtime_engine import SSOTRuntimeConfig, SSOTRuntimeEngine

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
        context_window_tokens=int(getattr(context_budget, "context_window_tokens", 0) or 0),
        max_input_tokens=int(getattr(context_budget, "max_input_tokens", 48_000) or 48_000),
        max_output_tokens=int(getattr(context_budget, "reserved_output_tokens", 4096) or 4096),
        context_safety_tokens=int(getattr(context_budget, "safety_tokens", 2048) or 2048),
    )
    registry = prebuilt_registry or _build_ssot_runtime_tool_registry(allowed_tool_ids)
    engine_kwargs: dict[str, Any] = {
        "config": config,
        "llm_invoke": _invoke_llm_for_ssot_runtime,
        "tool_registry": registry,
    }
    if emitter is not None:
        engine_kwargs["emitter"] = emitter
    engine_kwargs["approval_handler"] = _build_approval_handler(
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        emitter=emitter,
    )
    engine = SSOTRuntimeEngine(**engine_kwargs)
    client = _tool_runtime_client()

    for tool_id in registry:
        engine.register_tool(
            tool_id,
            _make_tool_handler(
                client=client,
                tool_id=tool_id,
                workspace_id=workspace_id,
                session_id=session_id,
                run_id=run_id,
                trace_id=trace_id,
                requested_by=requested_by,
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
):
    """Create the production approval pause/resume callback for QueryLoop."""

    async def _handle(ctx, gate: dict[str, Any]) -> bool:
        store = get_approval_store()
        approval_ids: list[str] = []
        details = list(gate.get("approval_details") or [])
        for detail in details:
            tool_id = str(detail.get("tool") or "unknown")
            reason = str(detail.get("risk_reason") or "高危操作需要确认")
            command = str(detail.get("command") or "")
            description = f"{reason}: {tool_id}"
            if command:
                description += f" → {command[:120]}"
            req = store.create(
                session_id=session_id,
                tool_id=tool_id,
                arguments=detail,
                description=description,
                risk_level=str(gate.get("risk_level") or "high"),
                workspace_id=workspace_id,
                run_id=run_id,
            )
            approval_ids.append(req.approval_id)

        if not details:
            nodes = list(gate.get("approval_nodes") or [])
            req = store.create(
                session_id=session_id,
                tool_id=", ".join(nodes) or "unknown",
                arguments={"nodes": nodes},
                description=str(gate.get("message") or "高危操作需要确认"),
                risk_level=str(gate.get("risk_level") or "high"),
                workspace_id=workspace_id,
                run_id=run_id,
            )
            approval_ids.append(req.approval_id)

        event = {
            "approval_ids": approval_ids,
            "risk_level": str(gate.get("risk_level") or "high"),
            "status": "pending",
        }
        ctx.extras.setdefault("approval_events", []).append(event)
        if emitter is not None:
            emitter.emit("approval_waiting", event)

        decisions = await asyncio.gather(*(
            asyncio.to_thread(
                store.wait,
                approval_id,
                blocking=True,
                timeout=_APPROVAL_WAIT_SECONDS,
            )
            for approval_id in approval_ids
        ))
        approved = bool(approval_ids) and all(bool(value) for value in decisions)
        resolved_event = {**event, "status": "approved" if approved else "rejected"}
        ctx.extras.setdefault("approval_events", []).append(resolved_event)
        if emitter is not None:
            emitter.emit("approval_resolved", resolved_event)
        return approved

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
        tools[tool_id] = {
            "description": str(item.get("description") or tool_id),
            "args_schema": item.get("input_schema") or {},
            "category": item.get("category") or "",
            "risk_level": item.get("risk_level") or "low",
            "action_profiles": action_profiles.get(tool_id, []),
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


def _make_tool_handler(
    *,
    client,
    tool_id: str,
    workspace_id: str,
    session_id: str,
    run_id: str,
    trace_id: str,
    requested_by: str,
):
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        from core.tools.context import ToolRuntimeContext

        ctx = ToolRuntimeContext(
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            requested_by=requested_by,
            module="ssot_runtime",
        )
        args = dict(args or {})
        # Inject runtime context into args so tool handlers can access
        # session_id / run_id without relying on ToolInvocation fields
        result = await asyncio.to_thread(client.invoke, tool_id, args, context=ctx)
        # ToolExecutor already returns the canonical, redacted payload. Keep it
        # flat so QueryLoop can consume control fields such as tracking and
        # content_complete without a second lossy wrapper.
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


def _invoke_llm_for_ssot_runtime(**kwargs):
    from agent.llm.runtime import invoke_llm
    from agent.runtime.token_tracker import record_llm_call

    system = str(kwargs.get("system") or "")
    user = str(kwargs.get("user") or "")
    caller_extra = kwargs.get("extra") or {}
    stream_scope = str(caller_extra.get("stream_scope") or "internal").lower()
    is_planner = stream_scope == "planner"
    # Preserve an explicit empty list: QueryLoop uses it for final-response-only
    # calls where the model must synthesize existing results without tools.
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

    resp = invoke_llm(
        task="assistant_chat",
        messages=[
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ],
        tools=tools,
        user_input=user,
        extra=extra,
        config_override=config_override,
    )

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
    # both native function calls and textual {nodes: [...]} plans.
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

    # v3.16: if the final response is a known placeholder but we
    # have actual tool results, return empty string — let the caller
    # do a runtime-aware retry instead of surfacing a useless stub.
    if text and _is_bogus_final(text):
        text = ""

    if text:
        return text
    # No tool results and no text — return empty so caller can fall back.
    return ""


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
    for node_id, tr in (runtime_result.node_results or {}).items():
        events.append({
            "type": "tool_call",
            "name": "tool_call",
            "tool_id": tr.tool,
            "node_id": node_id,
            "trace_id": trace_id,
            "run_id": turn_id,
            "timestamp": time.time(),
            "status": "started",
        })
        events.append({
            "type": "tool_result",
            "name": "tool_result",
            "tool_id": tr.tool,
            "node_id": node_id,
            "trace_id": trace_id,
            "run_id": turn_id,
            "timestamp": time.time(),
            "status": "success" if tr.success else "failed",
            "ok": bool(tr.success),
            "summary": _tool_summary(tr.data if isinstance(tr.data, dict) else {}, tr),
            "duration_ms": tr.latency_ms,
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
    "前面", "之前", "上次", "刚才", "继续", "还记得", "记得",
    "那个", "上一轮", "前一轮", "前面的", "之前的", "刚才的",
)
def _build_retrieved_context_block(
    *, workspace_id: str, session_id: str, task_id: str, user_input: str,
    max_tokens: int = 3000,
) -> str:
    """Retrieve concise governed memory and knowledge context for this turn."""
    if not workspace_id or not user_input.strip():
        return ""
    try:
        from core.context.unified_retriever import get_retriever
        from storage.memory_governance import MemoryStore

        retrieved = get_retriever(workspace_id).retrieve_for_context(
            user_input,
            top_k_memory=3,
            top_k_knowledge=2,
            session_id=session_id,
            task_id=task_id,
        )
        from core.runtime_engine.context_budget import truncate_text_to_tokens

        lines: list[str] = []
        item_tokens = max(200, min(750, max_tokens // 3))
        core_rules = MemoryStore().list_retrievable(
            workspace_id,
            memory_type="core_rule",
            limit=8,
        )
        for rule in core_rules:
            content = str(rule.get("content") or rule.get("summary") or "").strip()
            if content:
                compacted, _ = truncate_text_to_tokens(content, min(item_tokens, 350))
                lines.append(f"[core-rule authority=explicit-user] {compacted}")
        for hit in retrieved.get("memory_hits", [])[:3]:
            if str(hit.get("memory_type") or "") == "core_rule":
                continue
            content = str(hit.get("content") or hit.get("summary") or "").strip()
            if content:
                compacted, _ = truncate_text_to_tokens(content, item_tokens)
                lines.append(f"[memory] {compacted}")
        for hit in retrieved.get("knowledge_hits", [])[:2]:
            content = str(hit.get("content") or hit.get("summary") or "").strip()
            if content:
                compacted, _ = truncate_text_to_tokens(content, item_tokens)
                lines.append(f"[knowledge] {compacted}")
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
        messages = _load_context_messages(session)
        if not messages:
            return ""

        from core.runtime_engine.context_budget import estimate_text_tokens, truncate_text_to_tokens

        recent = messages[-_HISTORY_RECENT_MESSAGES:]
        older = messages[:-_HISTORY_RECENT_MESSAGES]
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
        retrieved = _retrieve_history_references(messages, user_input)
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


def _load_context_messages(session) -> list[dict[str, str]]:
    persisted: list[dict[str, str]] = []
    persisted_seen: set[str] = set()
    ws_id = str(getattr(session, "workspace_id", "") or "")
    session_id = str(getattr(session, "session_id", "") or "")
    if ws_id and session_id:
        try:
            from storage.message_store import SessionMessageStore

            for m in SessionMessageStore(session_id=session_id, ws_id=ws_id).get_messages():
                _append_context_message(persisted, persisted_seen, m)
        except Exception:
            _LOG.debug("SessionMessageStore history read failed for %s", session_id, exc_info=True)

    memory: list[dict[str, str]] = []
    memory_seen: set[str] = set()
    for i, msg in enumerate(list(getattr(session, "history", None) or [])):
        role = str(getattr(msg, "role", "") or "")
        content = str(getattr(msg, "content", "") or "")
        _append_context_message(memory, memory_seen, {
            "message_id": getattr(msg, "id", "") or getattr(msg, "message_id", "") or f"mem:{i}:{role}:{content[:40]}",
            "role": role,
            "content": content,
        })
    overlap = _history_overlap(persisted, memory)
    return persisted + memory[overlap:]


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


def _append_context_message(messages: list[dict[str, str]], seen: set[str], raw: Any) -> None:
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
    messages.append({"role": role, "content": content})


def _summarize_older_messages(
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
) -> str:
    from core.runtime_engine.context_budget import estimate_text_tokens, truncate_text_to_tokens

    lines: list[str] = []
    for m in messages:
        content = m["content"]
        if _looks_context_important(content):
            compacted, _ = truncate_text_to_tokens(content, min(180, max_tokens))
            lines.append(f"  [{m['role']}] {compacted}")
        if estimate_text_tokens("\n".join(lines)) >= max_tokens:
            break
    if not lines and messages:
        sample = messages[:3] + messages[-3:]
        for m in sample:
            compacted, _ = truncate_text_to_tokens(m["content"], min(120, max_tokens))
            lines.append(f"  [{m['role']}] {compacted}")
    if not lines:
        return ""
    compacted, _ = truncate_text_to_tokens("\n".join(lines), max_tokens)
    return compacted


def _retrieve_history_references(messages: list[dict[str, str]], user_input: str) -> list[dict[str, str]]:
    text = (user_input or "").strip()
    if not text or not any(p in text for p in _HISTORY_REFERENCE_PATTERNS):
        return []
    terms = {
        token.strip("，。,.、：:；;（）()[]【】\"'")
        for token in text.replace("/", " ").replace("-", " ").split()
        if len(token.strip()) >= 2
    }
    important: list[dict[str, str]] = []
    for m in messages[:-_HISTORY_RECENT_MESSAGES]:
        content = m["content"]
        if (terms and any(t in content for t in terms)) or _looks_context_important(content):
            important.append(m)
    return important[-8:]


def _looks_context_important(text: str) -> bool:
    markers = (
        "报告", "文件", "数据", "任务", "异常", "记住", "总结", "结论",
        "错误", "失败", "制品", "知识库", "上下文",
    )
    return any(m in text for m in markers)


# ── Session history sync ──────────────────────────────────────

def _sync_session_history(session, user_input: str, final_response: str) -> None:
    """Append current turn to session.history for context in next turns."""
    try:
        from agent.protocol.message import UserMessage, AssistantMessage

        history = getattr(session, "history", None)
        if history is None:
            history = []
            session.history = history

        # Dedup check: skip if last entries already match
        if len(history) >= 2:
            last_user = history[-2]
            last_asst = history[-1]
            if (getattr(last_user, "role", "") == "user"
                and getattr(last_asst, "role", "") == "assistant"
                and getattr(last_user, "content", "") == user_input
                and getattr(last_asst, "content", "") == final_response):
                return

        if not final_response:
            return

        history.append(UserMessage(content=user_input))
        history.append(AssistantMessage(content=final_response))
    except Exception:
        logging.getLogger(__name__).warning("Failed to sync session history", exc_info=True)
