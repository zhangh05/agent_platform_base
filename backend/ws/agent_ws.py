"""WebSocket handler for real-time agent streaming.

Design:
- WebSocket endpoint: /ws/agent
- Client sends JSON messages, server pushes live StreamEmitter events.
- Business execution still goes through AgentApp.submit_user_message(), so
  HTTP and WebSocket share the same Agent Runtime contract.
- Job lifecycle (create, update runs, progress) is handled here, mirroring
  the HTTP route in agent_routes.py.

Message protocol:
  Client → Server:
    {"type": "message", "stream_id": "uuid", "user_input": "...", ...}
    {"type": "resume", "stream_id": "uuid", "after_seq": 12, ...}
    {"type": "cancel", "stream_id": "uuid", ...}

  Server → Client:
    {"type": "accepted", "stream_id": "uuid", "resumed": false}
    {"type": "event", "name": "...", "data": {...}}  — live event
    {"type": "done", "final_response": "...", "session_id": "...", "turn_id": "...", "tool_calls_count": 0}
    {"type": "error", "message": "..."}

Transport disconnect is not cancellation. Active turns are buffered in the
single backend process for replay after a browser refresh; user cancellation
must use the explicit ``cancel`` frame.
"""

import json
import logging
import queue
import threading
import time
import traceback
import uuid
from flask import request
from flask_sock import Sock
from backend.core.auth import is_allowed_browser_origin

sock = Sock()
_log = logging.getLogger("ws.agent")
_MAX_WS_INPUT_LENGTH = 262144  # 256KB — supports long user inputs
_MAX_WS_METADATA_JSON = 16384
_WS_HEARTBEAT_INTERVAL_SECONDS = 2.0
_WS_STREAM_RETENTION_SECONDS = 600.0
_WS_STREAM_MAX_EVENTS = 4000


def _heartbeat_payload(started_at: float, now: float | None = None) -> dict:
    """Build a lightweight liveness event while an agent turn is quiet."""
    current = time.monotonic() if now is None else now
    return {
        "type": "event",
        "name": "heartbeat",
        "data": {
            "type": "heartbeat",
            "elapsed_ms": max(0, int((current - started_at) * 1000)),
        },
    }


class _ResumableTurnStream:
    """In-memory replay buffer whose lifetime is independent of one socket.

    The platform currently runs one backend process. Keeping active streams in
    this process lets a refreshed browser re-attach without creating a second
    Agent turn. Completed streams are retained briefly so a reconnect racing
    with completion can still receive the terminal frame.
    """

    def __init__(self, stream_id: str, owner: str, workspace_id: str, session_id: str):
        self.stream_id = stream_id
        self.owner = owner
        self.workspace_id = workspace_id
        self.session_id = session_id
        self.started_at = time.monotonic()
        self.completed_at: float | None = None
        self.cancel_event = threading.Event()
        self._condition = threading.Condition()
        self._events: list[dict] = []
        self._sequence = 0
        self._terminal = False

    def put(self, event, timeout=None) -> None:  # queue-compatible worker sink
        del timeout
        if event is None:
            with self._condition:
                self._condition.notify_all()
            return
        if not isinstance(event, dict):
            return
        with self._condition:
            self._sequence += 1
            framed = dict(event)
            framed["stream_id"] = self.stream_id
            framed["stream_seq"] = self._sequence
            self._events.append(framed)
            if len(self._events) > _WS_STREAM_MAX_EVENTS:
                del self._events[:len(self._events) - _WS_STREAM_MAX_EVENTS]
            if framed.get("type") in {"done", "error"}:
                self._terminal = True
                self.completed_at = time.monotonic()
            self._condition.notify_all()

    def put_nowait(self, event) -> None:
        self.put(event)

    def get_nowait(self):
        raise queue.Empty

    def events_after(self, sequence: int, timeout: float = 0.5) -> tuple[list[dict], bool, int]:
        with self._condition:
            if not self._terminal and self._sequence <= sequence:
                self._condition.wait(timeout=timeout)
            events = [event for event in self._events if int(event.get("stream_seq", 0)) > sequence]
            return events, self._terminal, self._sequence

    def request_cancel(self) -> None:
        self.cancel_event.set()


_resumable_turns: dict[str, _ResumableTurnStream] = {}
_resumable_turns_lock = threading.Lock()


def _stream_owner(username: str) -> str:
    return username or "__platform_api_token__"


def _validate_stream_id(raw) -> str:
    value = str(raw or "").strip()
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("invalid_stream_id")
    canonical = str(parsed)
    if value.lower() != canonical:
        raise ValueError("invalid_stream_id")
    return canonical


def _cleanup_resumable_turns(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    with _resumable_turns_lock:
        expired = [
            stream_id
            for stream_id, stream in _resumable_turns.items()
            if stream.completed_at is not None
            and current - stream.completed_at > _WS_STREAM_RETENTION_SECONDS
        ]
        for stream_id in expired:
            _resumable_turns.pop(stream_id, None)


def _lookup_resumable_turn(stream_id: str, owner: str, workspace_id: str) -> _ResumableTurnStream | None:
    _cleanup_resumable_turns()
    with _resumable_turns_lock:
        stream = _resumable_turns.get(stream_id)
    if stream is None or stream.owner != owner or stream.workspace_id != workspace_id:
        return None
    return stream


def _register_resumable_turn(stream: _ResumableTurnStream) -> bool:
    _cleanup_resumable_turns()
    with _resumable_turns_lock:
        if stream.stream_id in _resumable_turns:
            return False
        _resumable_turns[stream.stream_id] = stream
        return True


def _stream_turn_to_socket(ws, stream: _ResumableTurnStream, after_seq: int = 0) -> None:
    """Replay buffered frames, then follow new frames until terminal/disconnect."""
    cursor = max(0, int(after_seq or 0))
    next_heartbeat_at = time.monotonic() + _WS_HEARTBEAT_INTERVAL_SECONDS
    while True:
        events, terminal, latest_seq = stream.events_after(cursor, timeout=0.25)
        for event in events:
            ws.send(json.dumps(event, ensure_ascii=True, default=str))
            cursor = max(cursor, int(event.get("stream_seq", 0)))
            next_heartbeat_at = time.monotonic() + _WS_HEARTBEAT_INTERVAL_SECONDS
        if terminal and cursor >= latest_seq:
            return
        now = time.monotonic()
        if now >= next_heartbeat_at:
            heartbeat = _heartbeat_payload(stream.started_at, now)
            heartbeat["stream_id"] = stream.stream_id
            heartbeat["stream_seq"] = cursor
            ws.send(json.dumps(heartbeat, ensure_ascii=True))
            next_heartbeat_at = now + _WS_HEARTBEAT_INTERVAL_SECONDS


def _normalize_ws_attachments(username: str, workspace_id: str, raw):
    """Validate attachments under the authenticated user's storage scope."""
    from backend.core.chat_attachments import normalize_chat_attachments
    from storage.principal import storage_principal

    with storage_principal(username):
        return normalize_chat_attachments(workspace_id, raw)

# v3.16: Global connection registry for broadcasting system events
# (job_updated, run_status) to all active clients.
_active_ws_connections: dict[str, tuple[str, str, object]] = {}  # key → (username, workspace_id, ws)
_active_ws_lock = threading.Lock()


def broadcast_ws_event(event: dict) -> None:
    """Push a system event only to clients in the owning workspace."""
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    workspace_id = str(data.get("workspace_id") or "").strip()
    if not workspace_id:
        _log.warning("Dropped WebSocket broadcast without workspace_id: %s", event.get("name"))
        return
    from storage.principal import current_storage_principal
    username = current_storage_principal()
    payload = json.dumps({"type": "event", "name": event["name"], "data": event.get("data", {})}, ensure_ascii=True, default=str)
    dead: list[str] = []
    with _active_ws_lock:
        recipients = [
            (key, ws) for key, (owner, ws_id, ws) in _active_ws_connections.items()
            if ws_id == workspace_id and owner == username
        ]
    for key, ws in recipients:
        try:
            ws.send(payload)
        except Exception:
            dead.append(key)
    for key in dead:
        with _active_ws_lock:
            _active_ws_connections.pop(key, None)


def register_ws_routes(app):
    """Register WebSocket routes on the Flask app."""
    sock.init_app(app)

    @sock.route("/ws/agent")
    def ws_agent(ws):
        """WebSocket endpoint for agent message streaming."""
        if not _same_origin_ws_request():
            ws.send(json.dumps({"type": "error", "message": "csrf_origin_denied"}))
            return

        # When auth is enabled, enforce token on the first message
        _auth_checked = False
        authenticated_username = ""
        authenticated_role = ""
        authenticated_workspaces: list[str] = []
        ws_key = ""

        try:
            while True:
                raw = ws.receive(timeout=300)
                if raw is None:
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    ws.send(json.dumps({"type": "error", "message": "Invalid JSON"}, ensure_ascii=True))
                    continue

                # WebSocket routes are outside /api, so the Flask auth
                # middleware does not protect them. Authenticate the first
                # frame regardless of whether it is a ping or an agent turn.
                if not _auth_checked:
                    from backend.core.auth import _is_auth_enabled, _is_identity_enabled, _is_login_enabled, _get_api_token, is_current_session_authenticated
                    import hmac as _hmac
                    if not is_current_session_authenticated():
                        api_token = _get_api_token()
                        frame_token = str(msg.get("auth_token", ""))
                        has_valid_token = bool(api_token and _hmac.compare_digest(frame_token, api_token))
                        if (_is_login_enabled() or _is_identity_enabled()) and not has_valid_token:
                            ws.send(json.dumps({"type": "error", "message": "unauthorized"}))
                            return
                        if _is_auth_enabled() and api_token and not has_valid_token:
                            ws.send(json.dumps({"type": "error", "message": "unauthorized"}))
                            return
                    else:
                        from flask import session
                        authenticated_username = str(session.get("agent_platform_user") or "")
                        authenticated_role = str(session.get("agent_platform_role") or "viewer")
                        authenticated_workspaces = list(session.get("agent_platform_workspaces") or [])
                    _auth_checked = True

                # System WebSocket — register for broadcasts, skip agent turn
                if msg.get("type") == "ping":
                    workspace_id = str(msg.get("workspace_id") or "").strip()
                    try:
                        from storage.ids import validate_workspace_id
                        workspace_id = validate_workspace_id(workspace_id)
                    except ValueError:
                        ws.send(json.dumps({"type": "error", "message": "invalid_workspace_id"}))
                        continue
                    if not _ws_workspace_allowed(
                        authenticated_username,
                        authenticated_role,
                        authenticated_workspaces,
                        workspace_id,
                        write=False,
                    ):
                        ws.send(json.dumps({"type": "error", "message": "workspace_forbidden"}))
                        continue
                    ws_key = f"{id(ws)}_{threading.current_thread().ident}"
                    with _active_ws_lock:
                        _active_ws_connections[ws_key] = (authenticated_username, workspace_id, ws)
                    ws.send(json.dumps({"type": "pong", "message": "connected"}, ensure_ascii=True))
                    continue

                message_type = msg.get("type")
                if message_type not in {"message", "resume", "cancel"}:
                    ws.send(json.dumps({"type": "error", "message": f"Unknown type: {msg.get('type')}"}, ensure_ascii=True))
                    continue

                session_id = msg.get("session_id", "") or ""
                workspace_id = msg.get("workspace_id", "") or ""
                if not workspace_id:
                    ws.send(json.dumps({"type": "error", "message": "workspace_id is required"}, ensure_ascii=True))
                    continue
                try:
                    from storage.ids import validate_workspace_id, validate_session_id
                    workspace_id = validate_workspace_id(workspace_id)
                    if session_id:
                        session_id = validate_session_id(session_id)
                except ValueError:
                    ws.send(json.dumps({
                        "type": "error",
                        "message": "Invalid session_id or workspace_id",
                    }, ensure_ascii=True))
                    continue
                if not _ws_workspace_allowed(
                    authenticated_username,
                    authenticated_role,
                    authenticated_workspaces,
                    workspace_id,
                    write=True,
                ):
                    ws.send(json.dumps({"type": "error", "message": "workspace_forbidden"}))
                    continue

                try:
                    stream_id = _validate_stream_id(msg.get("stream_id"))
                except ValueError as exc:
                    ws.send(json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=True))
                    continue
                owner = _stream_owner(authenticated_username)

                if message_type == "resume":
                    stream = _lookup_resumable_turn(stream_id, owner, workspace_id)
                    if stream is None:
                        ws.send(json.dumps({"type": "error", "message": "stream_not_found"}, ensure_ascii=True))
                        continue
                    ws.send(json.dumps({
                        "type": "accepted",
                        "stream_id": stream_id,
                        "resumed": True,
                    }, ensure_ascii=True))
                    _stream_turn_to_socket(ws, stream, msg.get("after_seq", 0))
                    continue

                if message_type == "cancel":
                    stream = _lookup_resumable_turn(stream_id, owner, workspace_id)
                    if stream is None:
                        ws.send(json.dumps({"type": "error", "message": "stream_not_found"}, ensure_ascii=True))
                        continue
                    stream.request_cancel()
                    ws.send(json.dumps({
                        "type": "cancel_ack",
                        "stream_id": stream_id,
                    }, ensure_ascii=True))
                    return

                user_input = msg.get("user_input", msg.get("message", ""))
                if not user_input:
                    ws.send(json.dumps({"type": "error", "message": "Empty user_input"}, ensure_ascii=True))
                    continue
                if len(str(user_input)) > _MAX_WS_INPUT_LENGTH:
                    ws.send(json.dumps({"type": "error", "message": "message too long (max 256KB)"}, ensure_ascii=True))
                    continue

                # A repeated message carrying the same stream id is an
                # idempotent re-attach, never a second Agent execution.
                stream = _lookup_resumable_turn(stream_id, owner, workspace_id)
                if stream is not None:
                    ws.send(json.dumps({
                        "type": "accepted",
                        "stream_id": stream_id,
                        "resumed": True,
                    }, ensure_ascii=True))
                    _stream_turn_to_socket(ws, stream, msg.get("after_seq", 0))
                    continue

                metadata = msg.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                try:
                    metadata["attachments"] = _normalize_ws_attachments(
                        authenticated_username, workspace_id, metadata.get("attachments"),
                    )
                except ValueError as exc:
                    ws.send(json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=True))
                    continue
                try:
                    from backend.core.agent_contract import metadata_size, normalize_metadata
                    if metadata_size(metadata) > _MAX_WS_METADATA_JSON:
                        ws.send(json.dumps({"type": "error", "message": "metadata too large (max 16KB)"}, ensure_ascii=True))
                        continue
                except Exception:
                    _log.warning("WS metadata normalize failed, resetting to {}", exc_info=True)
                    metadata = {}
                from backend.core.agent_contract import normalize_metadata
                metadata = normalize_metadata(metadata, transport="websocket", stream_mode="live")

                stream = _ResumableTurnStream(stream_id, owner, workspace_id, session_id)
                if not _register_resumable_turn(stream):
                    ws.send(json.dumps({"type": "error", "message": "stream_conflict"}, ensure_ascii=True))
                    continue
                error_holder = {"error": None}
                stats = {"live_events": 0}
                thread = threading.Thread(
                    target=_run_agent_thread,
                    args=(
                        user_input, session_id, workspace_id, metadata,
                        stream, error_holder, stats, stream.cancel_event,
                        authenticated_username,
                    ),
                    daemon=True,
                )
                thread.start()
                ws.send(json.dumps({
                    "type": "accepted",
                    "stream_id": stream_id,
                    "resumed": False,
                }, ensure_ascii=True))
                _stream_turn_to_socket(ws, stream, msg.get("after_seq", 0))

        except Exception as e:
            try:
                ws.send(json.dumps({"type": "error", "message": f"WebSocket error: {str(e)[:200]}"}, ensure_ascii=True))
            except Exception:
                pass
        finally:
            if ws_key:
                with _active_ws_lock:
                    _active_ws_connections.pop(ws_key, None)

    return app


def _same_origin_ws_request() -> bool:
    origin = request.headers.get("Origin")
    return is_allowed_browser_origin(origin, request.host)


def _ws_workspace_allowed(username: str, role: str, allowed: list[str], workspace_id: str, *, write: bool) -> bool:
    """Mirror HTTP workspace RBAC for the WebSocket transport."""
    if not username:
        return True  # platform API token
    try:
        from backend.core.identity import can_access_workspace, get_user
        current = get_user(username)
        if current is None or role == "owner":
            return True
        return can_access_workspace(role, allowed, workspace_id, write=write)
    except Exception:
        return False


def _run_agent_thread(
    user_input, session_id, workspace_id, metadata, event_queue, error_holder,
    stats, cancel_event=None, username="",
):
    """Run agent in background thread through the shared AgentApp contract."""
    from agent.runtime.stream_emitter import StreamEmitter

    def put_terminal(event) -> None:
        """Publish a terminal frame without leaving the worker blocked forever."""
        try:
            event_queue.put(event, timeout=0.5)
            return
        except queue.Full:
            pass
        try:
            event_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            event_queue.put_nowait(event)
        except queue.Full:
            _log.error("WS terminal event dropped after queue recovery")

    def realtime_callback(event):
        try:
            live_count = int(stats.get("live_events", 0)) + 1
            stats["live_events"] = live_count
            seq = int(stats.get("event_seq", 0)) + 1
            stats["event_seq"] = seq
            if isinstance(event, dict) and event.get("type") == "token":
                try:
                    event_queue.put({"type": "token", "content": event.get("content", ""), "seq": seq}, timeout=0.2)
                except queue.Full:
                    pass
            else:
                name = event.get("type", event.get("name", "event")) if isinstance(event, dict) else "event"
                data = event
                # Only surface lightweight fields for live display; the
                # full result (including large output arrays) is carried
                # through the final 'done' payload and run persistence.
                if name in ("tool_call", "tool_result") and isinstance(event, dict):
                    summary = str(event.get("summary") or event.get("message") or "")
                    data = {
                        "type": event.get("type"),
                        "name": event.get("name", event.get("tool",
                                event.get("tool_id", ""))),
                        "tool_id": event.get("tool_id", event.get("name", "")),
                        "ok": event.get("ok", event.get("status") == "ok"),
                        "summary": summary[:8000] + ("..." if len(summary) > 8000 else ""),
                        "call_id": event.get("call_id", ""),
                    }
                event_queue.put({
                    "type": "event",
                    "name": name,
                    "data": data,
                    "seq": seq,
                }, timeout=0.2)
        except Exception:
            _log.warning("realtime_callback event push failed seq=%s", stats.get("event_seq"), exc_info=True)

    from storage.principal import storage_principal
    try:
        principal_scope = storage_principal(username)
        principal_scope.__enter__()
        # StreamEmitter stores callbacks thread-locally, so it must be set in
        # the same worker thread that runs AgentApp.submit_user_message().
        StreamEmitter.set_realtime_callback(realtime_callback)

        from agent.app.service import get_default_agent_app
        app = get_default_agent_app()

        runtime_metadata = dict(metadata or {})
        if cancel_event is not None:
            runtime_metadata["cancel_check"] = cancel_event.is_set
        result = app.submit_user_message(
            user_input=user_input,
            session_id=session_id,
            workspace_id=workspace_id,
            metadata=runtime_metadata,
        )

        result_payload = result.to_dict()

        # ── Job lifecycle (unified via jobs.lifecycle) ──
        effective_session_id = session_id or result_payload.get("session_id", "")
        if effective_session_id:
            try:
                from jobs.lifecycle import attach_run_to_session_job
                attach_run_to_session_job(
                    ws_id=workspace_id,
                    session_id=effective_session_id,
                    run_id=result_payload.get("turn_id", ""),
                    tool_call_count=len(result_payload.get("tool_calls", [])),
                    user_input=user_input,
                    run_ok=bool(result_payload.get("ok", not result_payload.get("errors"))),
                    error=str((result_payload.get("errors") or [""])[0]),
                )
            except Exception:
                _log.exception("WS job lifecycle error session=%s ws=%s", effective_session_id, workspace_id)

        if result_payload.get("final_response"):
            from agent.llm.runtime import sanitize_provider_output
            result_payload["final_response"], stripped = sanitize_provider_output(result_payload["final_response"])
            if stripped:
                result_payload.setdefault("metadata", {})["reasoning_stripped"] = True

        # Fallback: if no live events were emitted, replay collected events so
        # older runtime paths still produce observable progress data.
        if int(stats.get("live_events", 0)) == 0:
            for ev in result_payload.get("events", []):
                try:
                    event_queue.put({"type": "event", "name": ev.get("type", "event"), "data": ev}, timeout=0.5)
                except queue.Full:
                    pass

        tool_calls = result_payload.get("tool_calls", [])
        tool_calls_count = len(tool_calls) or len([
            e for e in result_payload.get("events", [])
            if e.get("type") == "tool_call"
        ])
        metadata_out = result_payload.get("metadata", {}) or {}
        metadata_out.setdefault("transport", "websocket")
        metadata_out.setdefault("stream_mode", "live" if int(stats.get("live_events", 0)) else "event_replay_fallback")

        resolved_session_id = result_payload.get("session_id") or session_id or ""

        # Send done event first — so frontend sees it immediately
        put_terminal({
            "type": "done",
            "session_id": resolved_session_id,
            "turn_id": result_payload.get("turn_id", ""),
            "trace_id": result_payload.get("trace_id", ""),
            "final_response": result_payload.get("final_response", ""),
            "events": result_payload.get("events", []),
            "tool_calls_count": tool_calls_count,
            "tool_calls": tool_calls,
            "metadata": metadata_out,
            "errors": result_payload.get("errors", []),
            "warnings": result_payload.get("warnings", []),
            "tool_decision": result_payload.get("tool_decision", {}),
            "no_tool_reason": result_payload.get("no_tool_reason", ""),
            "stream_seq": stats.get("event_seq", 0),
            "capability": result_payload.get("capability", ""),
            "error_type": result_payload.get("error_type", ""),
        })

    except Exception as e:
        traceback.print_exc()
        error_holder["error"] = str(e)[:500]
        put_terminal({"type": "error", "message": str(e)[:500]})
    finally:
        try:
            principal_scope.__exit__(None, None, None)
        except Exception:
            pass
        try:
            StreamEmitter.clear_realtime_callback()
        except Exception:
            pass
        try:
            event_queue.put(None, timeout=0.2)
        except queue.Full:
            # The receiver also exits once the worker is no longer alive and
            # the queue drains, so a saturated queue does not require blocking.
            pass
