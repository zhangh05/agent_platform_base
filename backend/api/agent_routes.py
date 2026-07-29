# backend/api/agent_routes.py
"""Agent API routes — unified single entry point."""

import logging
from flask import request, jsonify
from backend.core.responses import error_response

_log = logging.getLogger("agent_routes")


def _validated_ws_id(ws_id: str):
    from storage.ids import validate_workspace_id
    try:
        ws = validate_workspace_id(ws_id)
        if not ws:
            return "", _json_error("INVALID_WORKSPACE_ID", "invalid workspace_id", 400)
        return ws, None
    except ValueError:
        return "", _json_error("INVALID_WORKSPACE_ID", "invalid workspace_id", 400)


def _validated_session_id(sid: str):
    if not sid:
        return sid, None
    from storage.ids import validate_session_id
    try:
        s = validate_session_id(sid)
        if not s:
            return "", _json_error("INVALID_SESSION_ID", "invalid session_id", 400)
        return s, None
    except ValueError:
        return "", _json_error("INVALID_SESSION_ID", "invalid session_id", 400)


def _json_len(value) -> int:
    from backend.core.agent_contract import metadata_size
    return metadata_size(value)


def _source_config_too_large_response():
    return _json_error("SOURCE_CONFIG_TOO_LARGE", "source_config too large", 413)


def _json_error(code: str, message: str, status: int, details: dict | None = None):
    body, status_code = error_response(code, message, status, details)
    return jsonify(body), status_code


def _result_status(result: dict) -> int:
    error = result.get("error")
    if error == "source_config_too_large":
        return 413
    if error == "invalid_workspace_id":
        return 400
    return 200


def _resolve_stream_mode(data: dict) -> tuple[bool, str]:
    """Delegate to shared agent_contract helper."""
    from backend.core.agent_contract import resolve_stream_mode
    return resolve_stream_mode(data)


def _normalize_agent_result(result: dict, ws_id: str) -> dict:
    """Delegate to shared agent_contract helper."""
    from backend.core.agent_contract import normalize_agent_result
    return normalize_agent_result(result, ws_id)


def agent_message():
    """POST /api/agent/message — submit user message via AgentApp."""
    data = request.get_json(silent=True) or {}
    user_input = data.get("message", data.get("text", ""))
    stream, stream_mode = _resolve_stream_mode(data)

    # Validate workspace_id
    workspace_id = data.get("workspace_id", "")
    ws_id, ws_err = _validated_ws_id(workspace_id)
    if ws_err:
        return ws_err

    # Validate session_id (optional but must be valid format if provided)
    session_id = data.get("session_id", None)
    if session_id:
        sid, s_err = _validated_session_id(session_id)
        if s_err:
            return s_err
        session_id = sid

    if not user_input:
        return _json_error("BAD_REQUEST", "message is required", 400)

    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        return _json_error("BAD_REQUEST", "payload must be an object", 400)

    # Match the module runtime limit before dispatch so API status is stable.
    from backend.core.limits import source_config_too_large
    if payload.get("source_config") and source_config_too_large(str(payload.get("source_config", ""))):
        return _source_config_too_large_response()
    if source_config_too_large(str(user_input)):
        return _source_config_too_large_response()

    # Cap user input length to prevent OOM
    MAX_INPUT_LENGTH = 65536  # 64KB
    if len(user_input) > MAX_INPUT_LENGTH:
        return _json_error("PAYLOAD_TOO_LARGE", "message too long (max 64KB)", 413)

    metadata = data.get("metadata") or {}
    # Cap metadata size to prevent abuse
    try:
        meta_json = jsonify(metadata).get_data(as_text=True) if metadata else "{}"
        if len(meta_json) > 16384:
            return _json_error("PAYLOAD_TOO_LARGE", "metadata too large (max 16KB)", 413)
    except Exception:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    from backend.core.agent_contract import normalize_metadata
    metadata = normalize_metadata(metadata, transport="http", stream_mode=stream_mode)

    try:
        # All intents flow through LLM agentic loop
        from agent.app.service import get_default_agent_app
        app = get_default_agent_app()
        result = app.submit_user_message(
            user_input=user_input,
            session_id=session_id,
            workspace_id=ws_id,
            metadata=metadata,
        )
        if result is None:
            return jsonify({"ok": False, "error": "agent_no_result"}), 500
        result_payload = result.to_dict()
        result_payload = _normalize_agent_result(result_payload, ws_id)

        # ── Job: one per session, accumulating runs underneath ──
        effective_session_id = session_id or result_payload.get("session_id", "")
        if effective_session_id:
            try:
                from jobs.lifecycle import attach_run_to_session_job
                attach_run_to_session_job(
                    ws_id=ws_id,
                    session_id=effective_session_id,
                    run_id=result_payload.get("turn_id", ""),
                    tool_call_count=len(result_payload.get("tool_calls", [])),
                    user_input=user_input,
                )
            except Exception:
                _log.exception(
                    "Job lifecycle error for session=%s ws=%s user_input=%.80s",
                    effective_session_id, ws_id, user_input)

        result_payload.setdefault("metadata", {})["stream_mode"] = stream_mode
        if stream and stream_mode == "event_replay":
            warnings = result_payload.setdefault("warnings", [])
            msg = "stream_mode=event_replay: HTTP SSE replays collected events after turn completion; use WebSocket for live events."
            if msg not in warnings:
                warnings.append(msg)
        if result_payload.get("final_response"):
            from agent.llm.runtime import sanitize_provider_output
            result_payload["final_response"], stripped = sanitize_provider_output(result_payload["final_response"])
            if stripped:
                result_payload.setdefault("metadata", {})["reasoning_stripped"] = True

        if stream:
            return _stream_sse_response(result_payload, mode=stream_mode)

        return jsonify(result_payload)
    except Exception as e:
        _log.exception("agent_message failed")
        err_msg = str(e)[:500]
        return _json_error(
            "INTERNAL_ERROR",
            "agent execution failed",
            500,
            {"exception": err_msg, "type": type(e).__name__},
        )


# ── SSE helper ─────────────────────────────────────────────────────────

def _stream_sse_response(result: dict, mode: str = "event_replay"):
    """Replay AgentResult events as Server-Sent Events (SSE).

    This is intentionally named replay semantics: `agent_message()` has
    already completed before this function emits events. Use WebSocket when
    live model/tool streaming is required.
    """
    import json as _json
    from flask import Response

    def generate():
        meta = {
            "stream_mode": mode,
            "contract": "event_replay_after_turn_complete",
            "trace_id": result.get("trace_id", ""),
            "turn_id": result.get("turn_id", ""),
        }
        yield f"event: meta\ndata: {_json.dumps(meta, ensure_ascii=False)}\n\n"
        events = result.get("events", [])
        for ev in events:
            yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        final = {
            "ok": result.get("ok", True),
            "final_response": result.get("final_response", ""),
            "session_id": result.get("session_id", ""),
            "turn_id": result.get("turn_id", ""),
            "tool_calls_count": result.get("tool_calls_count", 0),
            "errors": result.get("errors", []),
            "warnings": result.get("warnings", []),
        }
        yield f"event: final\ndata: {_json.dumps(final, ensure_ascii=False)}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
