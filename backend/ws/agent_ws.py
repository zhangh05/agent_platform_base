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
    {"type": "message", "user_input": "...", "session_id": "...", "workspace_id": "default"}

  Server → Client:
    {"type": "event", "name": "...", "data": {...}}  — live event
    {"type": "done", "final_response": "...", "session_id": "...", "turn_id": "...", "tool_calls_count": 0}
    {"type": "error", "message": "..."}
"""

import json
import logging
import queue
import threading
import time
import traceback
from flask import request
from flask_sock import Sock
from backend.core.auth import is_allowed_browser_origin

sock = Sock()
_log = logging.getLogger("ws.agent")
_MAX_WS_INPUT_LENGTH = 262144  # 256KB — supports long user inputs
_MAX_WS_METADATA_JSON = 16384
_WS_HEARTBEAT_INTERVAL_SECONDS = 2.0


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
_active_turns: dict[tuple[str, str, str], threading.Event] = {}
_active_turns_lock = threading.Lock()

# Durable job updates are supplementary to the dedicated per-turn message
# WebSocket. Never let a slow dashboard listener synchronously stall the
# runtime callback that is producing tokens and stages.
_broadcast_pending: dict[str, tuple[str, str, str]] = {}
_broadcast_cv = threading.Condition()
_broadcast_worker_started = False


def _deliver_broadcast(username: str, workspace_id: str, payload: str) -> None:
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
    if dead:
        with _active_ws_lock:
            for key in dead:
                _active_ws_connections.pop(key, None)


def _broadcast_worker() -> None:
    while True:
        with _broadcast_cv:
            while not _broadcast_pending:
                _broadcast_cv.wait()
            _, (username, workspace_id, payload) = _broadcast_pending.popitem()
        _deliver_broadcast(username, workspace_id, payload)


def _enqueue_broadcast(username: str, workspace_id: str, coalesce_key: str, payload: str) -> None:
    global _broadcast_worker_started
    with _broadcast_cv:
        # Coalesce repeated lifecycle projections for the same durable object;
        # the newest snapshot is authoritative and prevents an unbounded queue.
        _broadcast_pending[coalesce_key] = (username, workspace_id, payload)
        if not _broadcast_worker_started:
            threading.Thread(target=_broadcast_worker, name="lzcore-ws-broadcast", daemon=True).start()
            _broadcast_worker_started = True
        _broadcast_cv.notify()


def request_active_turn_cancel(username: str, workspace_id: str, job_id: str) -> bool:
    """Signal the in-process runtime worker owned by this user/workspace."""
    key = (str(username or ""), str(workspace_id or ""), str(job_id or ""))
    with _active_turns_lock:
        cancel_event = _active_turns.get(key)
    if cancel_event is None:
        return False
    cancel_event.set()
    return True


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
    durable_id = str(data.get("job_id") or data.get("session_id") or event.get("name") or "event")
    _enqueue_broadcast(username, workspace_id, f"{username}:{workspace_id}:{durable_id}", payload)


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
        active_cancel_event = None

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
                        authenticated_username = str(session.get("lzcore_user") or "")
                        authenticated_role = str(session.get("lzcore_role") or "viewer")
                        authenticated_workspaces = list(session.get("lzcore_workspaces") or [])
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

                if msg.get("type") != "message":
                    ws.send(json.dumps({"type": "error", "message": f"Unknown type: {msg.get('type')}"}, ensure_ascii=True))
                    continue

                user_input = msg.get("user_input", msg.get("message", ""))
                if not user_input:
                    ws.send(json.dumps({"type": "error", "message": "Empty user_input"}, ensure_ascii=True))
                    continue
                if len(str(user_input)) > _MAX_WS_INPUT_LENGTH:
                    ws.send(json.dumps({"type": "error", "message": "message too long (max 64KB)"}, ensure_ascii=True))
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

                # Event queue for thread-safe communication
                event_queue = queue.Queue(maxsize=1000)
                error_holder = {"error": None}
                stats = {"live_events": 0}
                # A detached browser must not block the durable turn, while an
                # attached slow browser receives every frame through backpressure.
                transport_closed = threading.Event()

                active_cancel_event = threading.Event()
                thread = threading.Thread(
                    target=_run_agent_thread,
                    args=(
                        user_input, session_id, workspace_id, metadata,
                        event_queue, error_holder, stats, active_cancel_event,
                        authenticated_username, transport_closed,
                    ),
                    daemon=True,
                )
                thread.start()

                # Stream events from queue to WebSocket
                turn_started_at = time.monotonic()
                next_heartbeat_at = turn_started_at + _WS_HEARTBEAT_INTERVAL_SECONDS
                while True:
                    try:
                        event = event_queue.get(timeout=0.25)
                    except queue.Empty:
                        if not thread.is_alive():
                            try:
                                event = event_queue.get(timeout=0.5)
                            except queue.Empty:
                                break
                        else:
                            now = time.monotonic()
                            if now >= next_heartbeat_at:
                                try:
                                    ws.send(json.dumps(
                                        _heartbeat_payload(turn_started_at, now),
                                        ensure_ascii=True,
                                    ))
                                except Exception:
                                    # A browser refresh only detaches the viewer.
                                    # The durable turn continues and can be recovered
                                    # through the session job snapshot after reconnect.
                                    transport_closed.set()
                                    return
                                next_heartbeat_at = now + _WS_HEARTBEAT_INTERVAL_SECONDS
                            continue

                    if event is None:
                        break

                    try:
                        ws.send(json.dumps(event, ensure_ascii=True, default=str))
                        next_heartbeat_at = time.monotonic() + _WS_HEARTBEAT_INTERVAL_SECONDS
                    except Exception:
                        # Do not cancel business execution merely because the
                        # transport disappeared (refresh, sleep, network flap).
                        transport_closed.set()
                        return

                if error_holder["error"]:
                    try:
                        ws.send(json.dumps({"type": "error", "message": error_holder["error"]}, ensure_ascii=True))
                    except Exception:
                        pass
                active_cancel_event = None

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
    stats, cancel_event=None, username="", transport_closed=None,
):
    """Run agent in background thread through the shared AgentApp contract."""
    from agent.runtime.stream_emitter import StreamEmitter

    stats_lock = threading.Lock()
    # `asyncio.to_thread()` propagates ContextVars. A provider call that has
    # timed out can therefore retain this callback after the turn emits done.
    emission_lock = threading.Lock()
    emissions_open = threading.Event()
    emissions_open.set()
    transport_closed = transport_closed or threading.Event()

    def enqueue_live(event: dict | None) -> bool:
        """Apply transport backpressure without sacrificing durable execution.

        While the browser remains attached, every token, stage and terminal frame
        waits for queue capacity rather than being silently dropped. Once the
        receiver marks the connection detached, producer callbacks return
        immediately and the Agent turn keeps running for durable recovery.
        """
        while not transport_closed.is_set():
            try:
                event_queue.put(event, timeout=0.25)
                return True
            except queue.Full:
                continue
        return False

    def put_terminal(event) -> None:
        # Serialize terminal delivery with provider callbacks and permanently
        # reject callbacks copied into a late `asyncio.to_thread()` worker.
        with emission_lock:
            emissions_open.clear()
            enqueue_live(event)

    def realtime_callback(event):
        emission_lock.acquire()
        try:
            if not emissions_open.is_set():
                return
            with stats_lock:
                stats["live_events"] = int(stats.get("live_events", 0)) + 1
                seq = int(stats.get("event_seq", 0)) + 1
                stats["event_seq"] = seq
            if isinstance(event, dict) and event.get("type") == "token":
                enqueue_live({"type": "token", "content": event.get("content", ""), "seq": seq})
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
                if isinstance(data, dict):
                    data = {
                        **data,
                        "job_id": str(stats.get("job_id") or ""),
                        "client_request_id": str((metadata or {}).get("client_request_id") or ""),
                    }
                job_id_for_event = str(stats.get("job_id") or "")
                if job_id_for_event and isinstance(event, dict) and name != "heartbeat":
                    try:
                        from jobs.lifecycle import update_session_turn_stage
                        update_session_turn_stage(
                            workspace_id,
                            job_id_for_event,
                            session_id,
                            event,
                        )
                    except Exception:
                        _log.exception("unable to persist live stage job=%s stage=%s", job_id_for_event, name)
                enqueue_live({
                    "type": "event",
                    "name": name,
                    "data": data,
                    "seq": seq,
                })
        except Exception:
            _log.warning("realtime_callback event push failed seq=%s", stats.get("event_seq"), exc_info=True)
        finally:
            emission_lock.release()

    from storage.principal import storage_principal
    job_id = ""
    principal_scope = None
    client_request_id = str((metadata or {}).get("client_request_id") or "")
    try:
        principal_scope = storage_principal(username)
        principal_scope.__enter__()
        try:
            from jobs.lifecycle import claim_session_turn
            turn_claim = claim_session_turn(
                workspace_id, session_id, user_input,
                client_request_id=client_request_id,
            )
            job_id = str(turn_claim.job_id or "")
            if not turn_claim.should_execute:
                final_response = ""
                errors = [turn_claim.error] if turn_claim.error else []
                if turn_claim.status == "succeeded" and turn_claim.run_id:
                    from storage.run_record_store import get_run
                    final_response = str(
                        get_run(turn_claim.run_id, workspace_id).get(
                            "final_response_summary", "",
                        ) or "",
                    )
                elif turn_claim.status == "failed" and not errors:
                    errors = ["同一请求此前处理失败。"]
                put_terminal({
                    "type": "done",
                    "session_id": session_id,
                    "turn_id": turn_claim.run_id,
                    "trace_id": turn_claim.trace_id,
                    "final_response": final_response,
                    "events": [],
                    "tool_calls_count": 0,
                    "tool_calls": [],
                    "metadata": {
                        "transport": "websocket",
                        "idempotent": True,
                        "idempotent_redirect": {
                            "job_id": job_id,
                            "status": turn_claim.status or "running",
                        },
                    },
                    "errors": errors,
                    "warnings": [],
                    "tool_decision": {},
                    "no_tool_reason": "",
                    "stream_seq": stats.get("event_seq", 0),
                    "capability": "",
                    "error_type": "",
                })
                return
        except Exception:
            # Runtime execution remains available if the observational job
            # snapshot cannot be written; the failure is logged and never
            # replaced with fabricated progress.
            _log.exception("unable to start durable live turn session=%s", session_id)
            job_id = ""
        stats["job_id"] = job_id
        if job_id and cancel_event is not None:
            with _active_turns_lock:
                _active_turns[(username, workspace_id, job_id)] = cancel_event
            # The HTTP cancellation endpoint persists its intent before it
            # reaches this process-local map.  Replay that durable intent after
            # registration so a stop in the claim/register window is never
            # lost and frontend retries are not required for correctness.
            try:
                from jobs.store import get_job
                durable_job = get_job(workspace_id, job_id)
                if durable_job and bool(getattr(durable_job, "cancel_requested", False)):
                    cancel_event.set()
            except (OSError, RuntimeError, TypeError, ValueError):
                _log.warning(
                    "unable to restore durable cancellation ws=%s job=%s",
                    workspace_id, job_id, exc_info=True,
                )
        # StreamEmitter stores callbacks thread-locally, so it must be set in
        # the same worker thread that runs AgentApp.submit_user_message().
        StreamEmitter.set_realtime_callback(realtime_callback)

        from agent.app.service import get_default_agent_app
        app = get_default_agent_app()

        runtime_metadata = dict(metadata or {})
        runtime_control = None
        if cancel_event is not None:
            from core.runtime_engine.models import MainAgentRuntimeControl
            runtime_control = MainAgentRuntimeControl(cancel_check=cancel_event.is_set)
        result = app.submit_user_message(
            user_input=user_input,
            session_id=session_id,
            workspace_id=workspace_id,
            metadata=runtime_metadata,
            runtime_control=runtime_control,
        )

        result_payload = result.to_dict()

        # ── Job lifecycle (unified via jobs.lifecycle) ──
        effective_session_id = session_id or result_payload.get("session_id", "")
        if effective_session_id:
            try:
                from jobs.lifecycle import attach_run_to_session_job, finish_claimed_session_turn, finish_session_turn_snapshot
                finish_session_turn_snapshot(
                    workspace_id,
                    job_id,
                    effective_session_id,
                    client_request_id=client_request_id,
                    run_id=result_payload.get("turn_id", ""),
                    trace_id=result_payload.get("trace_id", ""),
                    ok=bool(result_payload.get("ok", not result_payload.get("errors"))),
                    error=str((result_payload.get("errors") or [""])[0]),
                )
                finish_claimed_session_turn(
                    workspace_id,
                    effective_session_id,
                    client_request_id=client_request_id,
                    job_id=job_id,
                    run_id=result_payload.get("turn_id", ""),
                    trace_id=result_payload.get("trace_id", ""),
                    ok=bool(result_payload.get("ok", not result_payload.get("errors"))),
                    error=str((result_payload.get("errors") or [""])[0]),
                )
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
                if not enqueue_live({"type": "event", "name": ev.get("type", "event"), "data": ev}):
                    break

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
        if job_id:
            try:
                from jobs.lifecycle import finish_claimed_session_turn, finish_session_turn_snapshot
                finish_session_turn_snapshot(
                    workspace_id,
                    job_id,
                    session_id,
                    client_request_id=client_request_id,
                    ok=False,
                    error=str(e),
                )
                finish_claimed_session_turn(
                    workspace_id,
                    session_id,
                    client_request_id=client_request_id,
                    job_id=job_id,
                    ok=False,
                    error=str(e),
                )
            except Exception:
                _log.exception("unable to persist failed live turn job=%s", job_id)
        put_terminal({"type": "error", "message": str(e)[:500]})
    finally:
        if job_id:
            with _active_turns_lock:
                _active_turns.pop((username, workspace_id, job_id), None)
        try:
            StreamEmitter.clear_realtime_callback()
        except Exception:
            pass
        if principal_scope is not None:
            try:
                principal_scope.__exit__(None, None, None)
            except Exception:
                pass
        enqueue_live(None)
