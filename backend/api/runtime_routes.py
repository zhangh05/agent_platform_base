# backend/api/runtime_routes.py
"""Runtime routes — diagnostics, selfcheck, retention, archive, tool invocation."""

import json
import logging
import os
import threading
from collections import OrderedDict

from flask import jsonify, request

from storage.ids import validate_workspace_id

_LOG = logging.getLogger(__name__)




def _invalid_ws():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _validated_ws_id(raw=None):
    """Validate workspace_id. Returns (ws_id, None) or (None, error_response).
    
    No implicit default — caller must provide a valid workspace_id.
    """
    if not raw:
        return None, _invalid_ws()
    try:
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_ws()




def register_runtime_routes(app):
    """Register all runtime API routes on the Flask app."""

    @app.route("/api/runtime/summary")
    def api_runtime_summary():
        """Return safe runtime counts used by the workbench status UI."""
        from agent.capabilities import catalog
        from core.tools.integration import get_default_tool_runtime_client

        caps = catalog.list_all()
        cap_counts = {
            "total": len(caps),
            "enabled": len(caps),
        }

        all_tools = get_default_tool_runtime_client().list_tools()
        visible_tools = [
            tool for tool in all_tools
            if tool.get("enabled", True)
            and not tool.get("forbidden", False)
            and tool.get("callable_by_llm", True)
        ]
        hidden_or_non_llm = [
            tool.get("tool_id", "")
            for tool in all_tools
            if not tool.get("enabled", True)
            or tool.get("forbidden", False)
            or not tool.get("callable_by_llm", True)
        ]

        return jsonify({
            "capabilities": cap_counts,
            "tools": {
                "registered": len(all_tools),
                "model_visible": len(visible_tools),
                "hidden_or_non_llm": hidden_or_non_llm,
            },
        })

    @app.route("/api/runtime/health")
    def api_runtime_health():
        from core.runtime.diagnostics import get_diagnostics
        ws_id = request.args.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        report = get_diagnostics(ws_id)
        return jsonify(report.as_dict())

    @app.route("/api/runtime/selfcheck")
    def api_runtime_selfcheck():
        ws_id = request.args.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from core.runtime.selfcheck import run_selfcheck
        result = run_selfcheck(ws_id)
        return jsonify(result.as_dict())

    @app.route("/api/tools/catalog")
    def api_tools_catalog():
        """Return read-only tool catalog — canonical IDs only."""
        from core.tools.catalog_snapshot import build_catalog_snapshot
        return jsonify(build_catalog_snapshot())

    @app.route("/api/tools/dry-run", methods=["POST"])
    def api_tools_dry_run():
        """Preview a tool invocation without executing it. canonical ID only.

        v3.0 contract: only canonical tool_ids are accepted. The
        response envelope is canonical-only:
          ok, dry_run, tool_id, canonical_tool_id, governance_status,
          risk_level, requires_approval, params, would_do, note

        Unknown tool_ids return:
          ok=false, error="unknown_tool_id",
          message="Only canonical tool_id is supported."
        """
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err

        body = request.get_json(silent=True) or {}
        requested_tool_id = body.get("tool_id", "")
        arguments = body.get("arguments", {})

        if not requested_tool_id:
            return jsonify({"ok": False, "error": "tool_id is required"}), 400

        from core.tools.canonical_registry import CANONICAL_REGISTRY
        from core.tools.integration import get_default_tool_runtime_client

        if requested_tool_id not in CANONICAL_REGISTRY:
            return jsonify({
                "ok": False,
                "error": "unknown_tool_id",
                "message": "Only canonical tool_id is supported.",
            }), 400

        gov = None  # v3.9.3: governance removed
        if gov and gov.status == "forbidden":
            return jsonify({
                "ok": False,
                "error": "forbidden_tool_id",
                "message": gov.reason,
            }), 403

        client = get_default_tool_runtime_client()
        spec = client._registry.get_tool(requested_tool_id)
        if not spec:
            return jsonify({"ok": False, "error": "tool not found"}), 404

        if not spec.dry_run_supported:
            return jsonify({"ok": False, "error": "dry_run not supported for this tool"}), 400

        from core.tools.schemas import ToolInvocation
        invocation = ToolInvocation(
            tool_id=requested_tool_id,
            arguments=arguments,
            workspace_id=ws_id,
            dry_run=True,
            requested_by="rest_api",
        )
        policy_decision = client._policy.check(spec, invocation)

        return jsonify({
            "ok": True,
            "dry_run": True,
            "workspace_id": ws_id,
            "tool_id": requested_tool_id,
            "canonical_tool_id": requested_tool_id,
            "governance_status": gov.status if gov else "active",
            "risk_level": policy_decision.risk_level or spec.risk_level,
            "requires_approval": bool(policy_decision.requires_approval),
            "policy_decision": policy_decision.__dict__,
            "params": list(arguments.keys()),
            "would_do": f"Would invoke {requested_tool_id} with {len(arguments)} argument(s)",
            "note": "This is a preview. The tool will NOT be executed.",
        })

    # ── Permissions ──
    @app.route("/api/tools/permissions")
    def api_tools_permissions():
        """Get workspace-level tool permissions."""
        ws_id = request.args.get("workspace_id", "")
        try:
            ws_id = validate_workspace_id(ws_id)
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400

        from core.tools.integration import get_default_tool_runtime_client
        client = get_default_tool_runtime_client()
        tools = client.list_tools()

        permissions = {
            "workspace_id": ws_id,
            "tools": [],
            "forbidden_count": 0,
            "high_risk_count": 0,
            "approval_required_count": 0,
        }
        for t in tools:
            perm = {
                "tool_id": t["tool_id"],
                "enabled": t.get("enabled", True),
                "risk_level": t.get("risk_level", "low"),
                "requires_approval": t.get("requires_approval", False),
            }
            permissions["tools"].append(perm)
            if t.get("risk_level") == "forbidden":
                permissions["forbidden_count"] += 1
            if t.get("risk_level") == "high":
                permissions["high_risk_count"] += 1
            if t.get("requires_approval"):
                permissions["approval_required_count"] += 1

        return jsonify(permissions)

    @app.route("/api/workspaces/<ws_id>/selfcheck")
    def api_workspace_selfcheck(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from core.runtime.selfcheck import run_selfcheck
        result = run_selfcheck(ws_id)
        return jsonify(result.as_dict())

    # ── Retention ──
    @app.route("/api/workspaces/<ws_id>/retention/preview")
    def api_workspace_retention_preview(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from core.runtime.retention import preview_retention, default_retention_policy
        preview = preview_retention(ws_id, default_retention_policy())
        return jsonify(preview.as_dict())

    @app.route("/api/workspaces/<ws_id>/retention/apply", methods=["POST"])
    def api_workspace_retention_apply(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        dry_run = (request.json or {}).get("dry_run", True) if request.is_json else True
        confirm = (request.json or {}).get("confirm", False) if request.is_json else False
        from core.runtime.retention import apply_retention, default_retention_policy
        preview = apply_retention(ws_id, default_retention_policy(),
                                  dry_run=dry_run, confirm=confirm)
        return jsonify(preview.as_dict())

    @app.route("/api/workspaces/<ws_id>/retention/audits")
    def api_workspace_retention_audits(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from core.runtime.retention import get_audits
        audits = get_audits(ws_id)
        return jsonify({"audits": audits})

    @app.route("/api/workspaces/<ws_id>/retention/audits/<audit_id>")
    def api_workspace_retention_audit(ws_id, audit_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from core.runtime.retention import get_audit
        audit = get_audit(ws_id, audit_id)
        if not audit:
            return jsonify({"ok": False, "error": "audit not found"}), 404
        return jsonify(audit)

    # ── Archive ──
    @app.route("/api/workspaces/<ws_id>/archive/preview")
    def api_archive_preview(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from core.runtime.archive import preview_archive_candidates, default_archive_policy
        preview = preview_archive_candidates(ws_id, default_archive_policy())
        return jsonify(preview.as_dict())

    @app.route("/api/workspaces/<ws_id>/archive/apply", methods=["POST"])
    def api_archive_apply(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        dry_run = (request.json or {}).get("dry_run", True) if request.is_json else True
        confirm = (request.json or {}).get("confirm", False) if request.is_json else False
        from core.runtime.archive import apply_archive, default_archive_policy
        result = apply_archive(ws_id, default_archive_policy(),
                               dry_run=dry_run, confirm=confirm)
        return jsonify(result.as_dict())

    @app.route("/api/workspaces/<ws_id>/archive/audits")
    def api_archive_audits(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from core.runtime.archive import get_archive_audits
        audits = get_archive_audits(ws_id)
        return jsonify({"audits": audits})

    @app.route("/api/workspaces/<ws_id>/archive/audits/<audit_id>")
    def api_archive_audit(ws_id, audit_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from core.runtime.archive import get_archive_audit
        audit = get_archive_audit(ws_id, audit_id)
        if not audit:
            return jsonify({"ok": False, "error": "audit not found"}), 404
        return jsonify(audit)

    @app.route("/api/workspaces/<ws_id>/archive/items")
    def api_archive_items(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from core.runtime.archive import list_archived_items
        items = list_archived_items(ws_id)
        return jsonify({"ok": True, "items": items, "count": len(items)})

    @app.route("/api/workspaces/<ws_id>/archive/restore", methods=["POST"])
    def api_archive_restore(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        data = request.get_json(silent=True) or {}
        from core.runtime.archive import restore_archived_item
        result = restore_archived_item(
            ws_id,
            month=str(data.get("month") or ""),
            kind=str(data.get("kind") or ""),
            name=str(data.get("name") or ""),
            confirm=data.get("confirm") is True,
        )
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.route("/api/agent/sse/stream/<session_id>")
    def api_agent_sse_stream(session_id):
        """SSE streaming endpoint — live agent execution events."""
        from flask import Response, stream_with_context
        from agent.runtime.session_events import subscribe
        import json as _json

        try:
            from storage.ids import validate_session_id
            sid = validate_session_id(session_id)
        except Exception:
            return jsonify({"ok": False, "error": "invalid_session_id"}), 400

        raw_ws_id = request.args.get("workspace_id", "")
        if not raw_ws_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        ws_id, err = _validated_ws_id(raw_ws_id)
        if err:
            return err

        try:
            from storage.session_store import get_session
            if not get_session(sid, ws_id):
                return jsonify({"ok": False, "error": "session_not_found"}), 404
        except Exception:
            return jsonify({"ok": False, "error": "session_lookup_failed"}), 500

        def generate():
            connected = {"session_id": sid, "workspace_id": ws_id}
            yield f"event: connected\ndata: {_json.dumps(connected, ensure_ascii=False)}\n\n"
            import time as _time
            deadline = _time.time() + 3600  # 1 hour max
            while _time.time() < deadline:
                frame = subscribe(sid, timeout=30, workspace_id=ws_id)
                if frame:
                    yield frame
                else:
                    yield ": keepalive\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Accel-Buffering": "no",
            },
        )
