"""Tool approval API routes — pause/resume for high-risk tool calls.

v3.2.0 (Guardian): Expanded the approval API surface.
- GET  /api/agent/approvals/pending        — list pending approvals
- POST /api/agent/approvals/<id>/resolve   — resolve an approval
- GET  /api/agent/approvals/history        — audit history (resolved)
- GET  /api/agent/approvals/sse            — real-time event stream (SSE)
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Iterator

from flask import Response, jsonify, request, stream_with_context

_LOG = logging.getLogger(__name__)


def _admin_token_allowed() -> bool:
    """Check admin permission for approval resolution.

    - If AGENT_PLATFORM_ADMIN_TOKEN is configured, MUST provide X-Admin-Token.
    - Otherwise, only localhost (127.0.0.1 / ::1) is allowed.
    """
    expected = os.environ.get("AGENT_PLATFORM_ADMIN_TOKEN", "")
    if expected:
        supplied = request.headers.get("X-Admin-Token", "")
        if not supplied:
            return False
        import hmac
        return hmac.compare_digest(supplied, expected)
    else:
        client_ip = request.remote_addr
        return client_ip in ("127.0.0.1", "::1")


def register_approval_routes(app) -> None:
    """Register approval endpoints on the Flask app."""

    def _validated_ws_id(raw: str):
        if not raw:
            return "", (jsonify({"ok": False, "error": "workspace_id is required"}), 400)
        try:
            from storage.ids import validate_workspace_id
            return validate_workspace_id(raw), None
        except Exception:
            return "", (jsonify({"ok": False, "error": "invalid_workspace_id"}), 400)

    @app.route("/api/agent/approvals/pending")
    def api_approvals_pending():
        """GET pending approvals, filtered by workspace and optionally session."""
        from agent.approval import get_approval_store
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        store = get_approval_store(ws_id)
        session_id = request.args.get("session_id", "")
        pending = store.get_pending(session_id, workspace_id=ws_id)
        return jsonify({
            "ok": True,
            "pending": pending,
            "count": len(pending),
        })

    @app.route("/api/agent/approvals/<approval_id>/resolve", methods=["POST"])
    def api_approval_resolve(approval_id):
        """POST resolve an approval — body: {decision: approve|reject|edit_args|respond}."""
        if not _admin_token_allowed():
            return jsonify({"ok": False, "error": "admin_access_required"}), 403
        from agent.approval import get_approval_store
        data = request.get_json(silent=True) or {}

        # Require the current decision field.
        decision = str(data.get("decision", "")).strip()
        if decision not in ("approve", "reject", "edit_args", "respond", "respond_with_feedback"):
            return jsonify({"ok": False, "error": "decision required: approve|reject|edit_args|respond"}), 400

        resolver = str(data.get("resolver") or "user")
        feedback = str(data.get("feedback", data.get("reason", "")) or "")[:500]
        reason = feedback if decision in ("respond", "respond_with_feedback") else str(data.get("reason") or "")
        allowed = decision == "approve" or decision == "edit_args"
        ws_id, err = _validated_ws_id(str(data.get("workspace_id", "")))
        if err:
            return err
        store = get_approval_store(ws_id)

        pending_req = store.get_pending_request(approval_id, ws_id)
        pending_meta = getattr(pending_req, "metadata", None) or {}
        if pending_meta.get("workflow_id") and decision == "edit_args":
            return jsonify({
                "ok": False,
                "error": "workflow_edit_args_not_supported",
                "message": "Workflow approvals must be approved or rejected as the exact bound action.",
            }), 400

        req = store.resolve(approval_id, allowed, workspace_id=ws_id, resolver=resolver, reason=reason)
        if req is None:
            return jsonify({"ok": False, "error": "approval not found or already resolved"}), 404

        # v3.10 Phase 4: wire into durable runtime interrupt/resume
        runtime_result = None
        task_id = ""
        try:
            meta = getattr(req, 'metadata', None) or {}
            task_id = meta.get("task_id", "")
            ws_id = req.workspace_id if hasattr(req, 'workspace_id') else ""
            if task_id and ws_id:
                from agent.runtime.durable.interrupt import resume_after_approval
                runtime_result = resume_after_approval(
                    task_id=task_id, ws_id=ws_id, approval_id=approval_id,
                    decision=decision,
                    edited_args=data.get("edited_args"),
                    feedback=data.get("feedback", ""),
                    reason=reason,
                )
            elif meta.get("workflow_id") and meta.get("workflow_node_id") and req.run_id:
                if decision == "approve":
                    from workflows.service import resume_workflow_run
                    resumed = resume_workflow_run(ws_id, req.run_id, approval_id)
                    runtime_result = {"ok": resumed.get("status") == "succeeded", "workflow_run": resumed}
                else:
                    from workflows.service import reject_workflow_run
                    rejected = reject_workflow_run(ws_id, req.run_id, approval_id)
                    runtime_result = {"ok": rejected is not None, "workflow_run": rejected}
        except Exception as exc:  # noqa: BLE001 - final HTTP boundary must return a structured failure
            _LOG.warning("resume_after_approval failed approval=%s task=%s ws=%s (non-fatal)",
                         approval_id, task_id or "?", ws_id or "?", exc_info=True)
            runtime_result = {
                "ok": False,
                "error": "approval_resume_failed",
                "message": str(exc)[:500],
            }

        return jsonify({
            "ok": True,
            "approval_id": approval_id,
            "decision": decision,
            "feedback_recorded": decision in ("respond", "respond_with_feedback"),
            "runtime_result": runtime_result,
        })

    @app.route("/api/agent/approvals/history")
    def api_approvals_history():
        """GET resolved approval history (Guardian audit)."""
        from agent.approval import get_approval_store
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        store = get_approval_store(ws_id)
        session_id = request.args.get("session_id", "")
        tool_id = request.args.get("tool_id", "")
        try:
            limit = max(1, min(int(request.args.get("limit", "100")), 500))
        except (TypeError, ValueError):
            limit = 100
        try:
            since = float(request.args.get("since", "0") or 0)
        except (TypeError, ValueError):
            since = 0.0
        records = store.get_history(
            session_id=session_id, tool_id=tool_id,
            workspace_id=ws_id,
            limit=limit, since_ts=since,
        )
        return jsonify({
            "ok": True,
            "history": records,
            "count": len(records),
        })

    @app.route("/api/agent/approvals/sse")
    def api_approvals_sse():
        """Server-Sent Events stream of approval create/resolve events.

        Subscribes to the in-process event bus and forwards each event as
        an SSE 'message' frame. Replaces the frontend 5s polling loop.
        """
        from agent.approval import get_event_bus
        bus = get_event_bus()
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err

        # Per-connection queue: the bus puts events here, the SSE generator
        # yields them. A keepalive ping is emitted every 25s so proxies and
        # browsers don't drop the connection.
        q: "queue.Queue[dict]" = queue.Queue(maxsize=64)

        def _on_event(event) -> None:
            try:
                if event.workspace_id != ws_id:
                    return
                q.put_nowait({
                    "kind": event.kind,
                    "approval_id": event.approval_id,
                    "session_id": event.session_id,
                    "workspace_id": event.workspace_id,
                    "tool_id": event.tool_id,
                    "allowed": event.allowed,
                    "payload": event.payload,
                    "ts": time.time(),
                })
            except queue.Full:
                pass  # drop if client is too slow; keepalive still flows

        unsubscribe = bus.subscribe(_on_event)

        @stream_with_context
        def _stream() -> Iterator[bytes]:
            try:
                # Send an initial comment so the browser opens the stream.
                yield b": connected\n\n"
                while True:
                    try:
                        evt = q.get(timeout=25.0)
                        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n".encode("utf-8")
                    except queue.Empty:
                        # keepalive ping (SSE comment line — ignored by EventSource)
                        yield b": ping\n\n"
            except GeneratorExit:
                pass
            finally:
                unsubscribe()

        return Response(
            _stream(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-store, no-transform",
                "Referrer-Policy": "no-referrer",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
