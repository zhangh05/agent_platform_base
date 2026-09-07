"""Approval extension HTTP boundary and execution hand-off."""

from __future__ import annotations

from flask import jsonify, request

from extensions.approval import service


def _workspace() -> str:
    return str(request.args.get("workspace_id") or (request.get_json(silent=True) or {}).get("workspace_id") or "").strip()


def register_routes(app):
    @app.route("/api/extensions/approval/operations", methods=["GET"])
    def approval_operations():
        workspace_id = _workspace()
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        return jsonify({"ok": True, "operations": service.list_operations(
            workspace_id,
            session_id=str(request.args.get("session_id") or ""),
            status=str(request.args.get("status") or ""),
        )})

    @app.route("/api/extensions/approval/operations/<operation_id>", methods=["GET"])
    def approval_operation(operation_id):
        workspace_id = _workspace()
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        record = service.get_operation(workspace_id, operation_id)
        return jsonify({"ok": True, "operation": record}) if record else (jsonify({"ok": False, "error": "operation_not_found"}), 404)

    @app.route("/api/extensions/approval/operations/<operation_id>/decision", methods=["POST"])
    def approval_operation_decision(operation_id):
        workspace_id = _workspace()
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        payload = dict(request.get_json(silent=True) or {})
        try:
            record = service.decide_operation(
                workspace_id,
                operation_id,
                str(payload.get("decision") or ""),
                decided_by=str(payload.get("decided_by") or "user"),
                note=str(payload.get("note") or ""),
            )
            if record.get("status") != "approved":
                return jsonify({"ok": True, "operation": record})
            claimed = service.claim_execution(workspace_id, operation_id)
            if claimed.get("status") != "executing":
                return jsonify({"ok": False, "operation": claimed, "error": "operation_invalidated"}), 409
            from core.tools.context import ToolRuntimeContext
            from core.tools.integration import get_default_tool_runtime_client
            target = claimed.get("target") if isinstance(claimed.get("target"), dict) else {}
            connection = target.get("connection") if isinstance(target.get("connection"), dict) else {}
            skill = claimed.get("skill") if isinstance(claimed.get("skill"), dict) else {}
            try:
                result = get_default_tool_runtime_client().invoke(
                    claimed["tool_id"],
                    {
                        "action": claimed["action"],
                        "connection_id": str(connection.get("connection_id") or ""),
                        "commands": list(claimed.get("commands") or []),
                        "timeout": int(claimed.get("timeout") or 15),
                    },
                    context=ToolRuntimeContext(
                        workspace_id=workspace_id,
                        session_id=str(claimed.get("session_id") or ""),
                        run_id=str(claimed.get("run_id") or ""),
                        # The decision endpoint is an authenticated HTTP boundary;
                        # it still invokes through ToolRuntimeClient rather than
                        # calling an extension handler directly.
                        requested_by="rest_api",
                        skill=str(skill.get("skill_id") or ""),
                        skill_connection_ids=(str(connection.get("connection_id") or ""),),
                    ),
                )
            except Exception as exc:
                settled = service.settle_execution(workspace_id, operation_id, {
                    "ok": False,
                    "execution_may_continue": True,
                    "error": f"approval_execution_exception:{exc}",
                })
                return jsonify({"ok": False, "operation": settled, "error": "approval_execution_exception"})
            raw = result.output if isinstance(result.output, dict) else {}
            settled = service.settle_execution(workspace_id, operation_id, {
                **raw,
                "ok": result.status in {"succeeded", "dry_run"},
                "error": "; ".join(result.errors or []),
            })
            return jsonify({"ok": True, "operation": settled})
        except KeyError:
            return jsonify({"ok": False, "error": "operation_not_found"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400


def register():
    return {
        "register_routes": register_routes,
        "execution_interceptor": service.execution_interceptor,
    }
