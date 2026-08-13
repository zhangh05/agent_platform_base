"""Turn persistence — write run records, messages, and trace events to disk."""

import json
import logging
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from agent.runtime.utils import now_iso
from storage.message_store import SessionMessageStore
from storage.redaction import redact_text, redact_value
from storage.run_record_store import save_trace_record, update_run_record, write_run_record


_log = logging.getLogger(__name__)


def persist_run_record(session, turn, result, context) -> None:
    """Best-effort: persist this turn to storage/run_record_store so that
    it shows up in /api/sessions/<id>/messages for plan-C sync.

    v1.0.3.1: also writes full user/assistant messages to the
    SessionMessageStore, so chat history does NOT rely on the
    120/300-character summaries in run records.

    Never raises — persistence failure must not break the turn.
    """
    try:
        user_input = (turn.op.user_input if turn.op else "") or ""
        is_internal_session = bool(
            getattr(session, "is_sub_agent", False)
            or (context and getattr(context, "metadata", {}).get("is_sub_agent"))
        )
        record_user_input = "[internal subagent task]" if is_internal_session else user_input
        final_response = (result.final_response if result else "") or ""
        ws_id = session.workspace_id or ""
        run_id = turn.turn_id
        created_at = _created_at_for_turn(turn, context)

        skill_results = {}
        if result and getattr(result, "tool_calls", None):
            for tc in result.tool_calls or []:
                md = tc.get("metadata", {}) if isinstance(tc, dict) else {}
                for k in ("output", "summary", "warnings", "audit"):
                    if k in md:
                        skill_results[k] = md[k]

        result_metadata = (
            result.metadata if result and getattr(result, "metadata", None) else {}
        )
        context_metadata = context.metadata if context and context.metadata else {}
        llm_metadata = dict(context_metadata.get("llm", {}) or {})
        llm_metadata.update(result_metadata.get("llm", {}) or {})

        # Extract artifact refs from tool_calls for run_store persistence
        artifact_refs = []
        if result and getattr(result, "tool_calls", None):
            for tc in result.tool_calls:
                if not isinstance(tc, dict):
                    continue
                arts = tc.get("artifacts", [])
                for a in arts:
                    if isinstance(a, dict) and a.get("artifact_id"):
                        artifact_refs.append({
                            "artifact_id": a["artifact_id"],
                            "artifact_type": a.get("artifact_type", ""),
                            "title": a.get("title", ""),
                        })
                    elif isinstance(a, str):
                        artifact_refs.append({"artifact_id": a})

        state = SimpleNamespace(
            request_id=turn.turn_id,
            session_id=session.session_id,
            created_at=created_at,
            user_input=record_user_input,
            intent=(context.metadata.get("intent", "") if context and context.metadata else ""),
            context={
                "llm": llm_metadata,
                "capability_id": context_metadata.get("capability_id", ""),
                "memory_written": False,
                "workspace_updated": False,
                "artifact_refs": artifact_refs,
            },
            runtime_mode="ssot_runtime",
            final_response=final_response,
            warnings=(result.warnings if result and result.warnings else []),
            trace_id=(result.trace_id if result else ""),
            error=((result.errors[0] if result and result.errors else None)),
            # v3.9.1: expose the real AgentResult.ok / .errors so
            # storage.run_record_store._safe_status can derive the record's
            # `status` field from runtime truth (was previously always "ok"
            # because it read the skill_results dict instead).
            result_ok=(bool(result.ok) if result else None),
            result_errors=(list(result.errors) if result and result.errors else []),
            execution_outcome=(
                str((result.metadata or {}).get("execution_outcome") or "")
                if result else ""
            ),
            skill_results=skill_results,
            tool_results=skill_results,
        )
        write_run_record(state, ws_id)
        _merge_result_projection(run_id, ws_id, result, context)

        # v1.0.3.1: also persist full messages independently
        if session.session_id and not is_internal_session:
            from core.runtime_engine.context_compaction import build_history_state_record

            store = SessionMessageStore(session_id=session.session_id, ws_id=ws_id)
            is_approval_resume = bool(
                (getattr(turn.op, "metadata", {}) or {}).get("__approval_continuation_resume")
            )
            is_approval_pending = bool(
                (result_metadata.get("ssot_runtime") or {}).get("approval_required")
            )
            if user_input and not is_approval_resume:
                user_attachments = list((getattr(turn.op, "metadata", {}) or {}).get("attachments") or [])
                store.write_message(run_id, "user", user_input, metadata={
                    "created_at": state.created_at,
                    "intent": state.intent,
                    "attachments": user_attachments,
                    "history_state": build_history_state_record(
                        "user", user_input, references=user_attachments,
                    ),
                })
            if final_response and not is_approval_pending:
                history_tools = _history_tool_context(result)
                store.write_message(run_id, "assistant", final_response, metadata={
                    "created_at": state.created_at,
                    "intent": state.intent,
                    "trace_id": result.trace_id if result else "",
                    # The full audit remains in the run record and trace.
                    # Conversation recovery receives only bounded, redacted
                    # evidence breadcrumbs for a later follow-up turn.
                    "tool_context": history_tools,
                    "history_state": build_history_state_record(
                        "assistant",
                        final_response,
                        tool_context=history_tools,
                        references=artifact_refs,
                    ),
                })

        # v1.0.3.2: persist trace events to disk. Some provider paths do not
        # emit detailed events, but run/trace APIs still need a stable trace.
        if result:
            try:
                persist_trace(run_id, ws_id, result.events or _synthetic_trace_events(run_id, result))
            except Exception:
                _log.warning("persist_trace failed for run %s", run_id, exc_info=True)
    except Exception as e:
        _log.warning("persist_run_record failed for run %s: %s", run_id, e, exc_info=True)


def persist_trace(run_id: str, ws_id: str, events: list) -> None:
    """Write trace events to workspaces/<ws>/runs/<run_id>.trace.json."""
    normalized_events = redact_value(_normalize_trace_events(run_id, events))

    # ── P0: Separate real vs synthetic vs missing counts ──
    real_events = [e for e in normalized_events if not e.get("synthetic")]
    synthetic_events = [e for e in normalized_events if e.get("synthetic") and not e.get("missing")]
    missing_events = [e for e in normalized_events if e.get("synthetic") and e.get("missing")]

    record = {
        "trace_id": normalized_events[0].get("trace_id", run_id) if normalized_events else run_id,
        "run_id": run_id,
        "workspace_id": ws_id,
        "events": normalized_events,
        "event_count": len(normalized_events),
        "real_event_count": len(real_events),
        "synthetic_event_count": len(synthetic_events),
        "missing_event_count": len(missing_events),
        "node_count": len(normalized_events),
        "total_duration_ms": _trace_total_duration_ms(normalized_events),
        "persisted_at": now_iso(),
    }
    save_trace_record(ws_id, run_id, record)


def _trace_total_duration_ms(events: list) -> int:
    """Compute turn duration from normalized trace events.

    Real runtime events store epoch-second timestamps plus per-event durations.
    The most reliable total is the observed span from first event start to the
    latest event end. Synthetic traces without timestamps fall back to the sum of
    event durations.
    """
    starts: list[float] = []
    ends: list[float] = []
    duration_sum = 0.0
    for event in events or []:
        if not isinstance(event, dict):
            continue
        duration = _safe_float(event.get("duration_ms"))
        if duration > 0:
            duration_sum += duration
        timestamp = _safe_float(event.get("timestamp") or event.get("ts") or event.get("time"))
        if timestamp > 0:
            starts.append(timestamp * 1000.0)
            ends.append(timestamp * 1000.0 + max(duration, 0.0))
    if starts and ends:
        return max(0, int(round(max(ends) - min(starts))))
    return max(0, int(round(duration_sum)))


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _synthetic_trace_events(run_id: str, result) -> list:
    """Generate synthetic trace events when the provider emitted none.

    ALL events produced here carry synthetic: true — they are
    fallback records, not real execution events. Inspectors and
    run summaries MUST distinguish these from real events.
    """
    trace_id = getattr(result, "trace_id", "") or run_id
    reason = "no_real_trace_from_provider"
    return [
        {"name": "turn_start", "run_id": run_id, "trace_id": trace_id,
         "synthetic": True, "reason": reason},
        {"name": "model", "run_id": run_id, "trace_id": trace_id,
         "synthetic": True, "reason": reason},
        {"name": "final", "run_id": run_id, "trace_id": trace_id,
         "synthetic": True, "reason": reason},
    ]


def _normalize_trace_events(run_id: str, events: list) -> list:
    """Normalize persisted trace events to the current event contract."""
    normalized = []
    for event in list(events or []):
        if not isinstance(event, dict):
            continue
        item = dict(event)
        item.setdefault("name", item.get("type", "event"))
        item.setdefault("run_id", run_id)
        normalized.append(item)

    return normalized


def _merge_result_projection(run_id: str, ws_id: str, result, context) -> None:
    """Add turn-level runtime diagnostics to the run record.

    The base run store intentionally writes compact summaries. Runtime
    debugging needs actual tool, retry, tracking, and model response metadata.
    """
    if not result:
        return
    from storage.run_record_store import get_run
    record = get_run(run_id, ws_id)
    if not record:
        return
    try:
        result_dict = result.to_dict() if hasattr(result, "to_dict") else {}
    except Exception:
        _log.warning("Cannot serialize result projection for run %s", run_id, exc_info=True)
        result_dict = {}
    metadata = dict(result_dict.get("metadata") or {})
    if context and getattr(context, "metadata", None):
        for key in (
            "visible_tools", "selected_capabilities", "model_responses",
            "required_tool_retry_used", "visibility_violations",
        ):
            if key in context.metadata:
                metadata.setdefault(key, context.metadata[key])
    record.update({
        "ok": bool(result_dict.get("ok", True)),
        "run_id": result_dict.get("turn_id") or run_id,
        "turn_id": result_dict.get("turn_id") or run_id,
        "trace_id": result_dict.get("trace_id") or record.get("trace_id", ""),
        "tool_calls": _safe_tool_calls(result_dict.get("tool_calls") or []),
        "tool_decision": result_dict.get("tool_decision") or {},
        "no_tool_reason": result_dict.get("no_tool_reason") or "",
        "metadata": _safe_metadata(metadata),
        "timeline_summary": result_dict.get("timeline_summary") or metadata.get("timeline_summary") or {},
        "warnings": [redact_text(str(w))[:300] for w in list(result_dict.get("warnings") or [])[:20]],
        "warning_count": len(list(result_dict.get("warnings") or [])),
        "execution_outcome": str(metadata.get("execution_outcome") or "complete"),
    })
    if isinstance(record.get("result_counts"), dict):
        record["result_counts"]["warnings"] = record["warning_count"]
    # v3.9.1: keep `status` consistent with `ok`. If the initial write (via
    # _safe_status) computed a wrong value because it read skill_results
    # instead of the real AgentResult, correct it now that we have the truth.
    is_ok = bool(result_dict.get("ok", True))
    has_errors = bool(result_dict.get("errors"))
    execution_outcome = str(metadata.get("execution_outcome") or "")
    if not is_ok or has_errors:
        record["status"] = "error"
    elif execution_outcome == "partial":
        record["status"] = "partial"
    elif record.get("status") not in ("planned",):
        # Only flip to "ok" if the record wasn't explicitly marked planned.
        record["status"] = "ok"
    try:
        update_run_record(ws_id, run_id, record)
    except Exception:
        _log.warning("Cannot write result projection for run %s", run_id, exc_info=True)


def _safe_tool_calls(tool_calls: list, *, limit: int = 64) -> list:
    # Auto-tracking can emit dozens of status polls for one background task.
    # Keep the original model-requested call and only the latest poll for each
    # tracked call. Full poll history remains in trace/tracking_events; the run
    # projection should show the terminal outcome instead of twenty "running"
    # rows that hide it.
    compacted: list = []
    latest_poll_by_source: dict[str, tuple[int, dict]] = {}
    for index, call in enumerate(list(tool_calls or [])):
        if not isinstance(call, dict):
            continue
        call_id = str(call.get("call_id") or "")
        match = re.match(r"^(.*)_track_\d+$", call_id)
        if match:
            latest_poll_by_source[match.group(1)] = (index, call)
        else:
            compacted.append((index, call))
    compacted.extend(latest_poll_by_source.values())
    compacted.sort(key=lambda item: item[0])

    safe = []
    for _, call in compacted[:max(0, int(limit))]:
        safe.append({
            "call_id": str(call.get("call_id", ""))[:120],
            "tool_id": str(call.get("tool_id", ""))[:120],
            "ok": bool(call.get("ok", False)),
            "summary": redact_text(str(call.get("summary", "")))[:800],
            "errors": [redact_text(str(e))[:240] for e in list(call.get("errors") or [])[:5]],
            "warnings": [redact_text(str(w))[:240] for w in list(call.get("warnings") or [])[:5]],
            "metadata": _safe_metadata(call.get("metadata") or {}, max_depth=2),
        })
    return safe


def _history_tool_context(result) -> list[dict]:
    """Return bounded, redacted tool facts suitable for chat continuation."""
    if result is None:
        return []
    compact = _safe_tool_calls(
        list(getattr(result, "tool_calls", None) or []),
        limit=80,
    )
    limit = 8
    # Follow-up questions need terminal failures and the newest evidence more
    # often than the first calls in a long turn. Keep those facts while
    # retaining chronological order for readable conversation context.
    selected = {
        index for index, item in enumerate(compact)
        if not item["ok"]
    }
    if len(selected) > limit:
        selected = set(sorted(selected)[-limit:])
    for index in range(len(compact) - 1, -1, -1):
        if len(selected) >= limit:
            break
        selected.add(index)
    return [
        {
            "tool_id": item["tool_id"],
            "ok": item["ok"],
            "summary": item["summary"],
            "errors": item["errors"],
        }
        for index, item in enumerate(compact)
        if index in selected
    ]


def _safe_metadata(value, max_depth: int = 3):
    # Scalars keep their JSON types even at the depth boundary. Converting
    # booleans/lists to strings corrupts persisted orchestration metadata.
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        text = redact_text(str(value))
        return text[:2000] + ("...[truncated]" if len(text) > 2000 else "")
    if max_depth < 0:
        return [] if isinstance(value, (list, tuple)) else {}
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:40]:
            if _is_sensitive_key(str(key)):
                continue
            out[str(key)] = _safe_metadata(item, max_depth=max_depth - 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_safe_metadata(item, max_depth=max_depth - 1) for item in list(value)[:30]]
    return str(value)[:500]


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(part in lower for part in (
        "secret", "password", "token", "api_key", "authorization",
        "credential", "private_key", "source_config", "raw_config",
    ))


def _created_at_for_turn(turn, context) -> str:
    """Return a non-empty timestamp for run/session projections."""
    if context and getattr(context, "metadata", None):
        value = context.metadata.get("created_at")
        if value:
            return str(value)
    if turn and getattr(turn, "context", None):
        value = turn.context.get("created_at")
        if value:
            return str(value)
    if turn and getattr(turn, "op", None):
        value = getattr(turn.op, "created_at", None)
        if value:
            return str(value)
    return datetime.now(timezone.utc).isoformat()
