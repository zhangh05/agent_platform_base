"""Approval extension HTTP boundary and execution hand-off."""

from __future__ import annotations

from flask import jsonify, request
from storage.principal import ContextThreadPoolExecutor

from extensions.approval import service

_CONTINUATION_EXECUTOR = ContextThreadPoolExecutor(max_workers=2, thread_name_prefix="approval-resume")


def _workspace() -> str:
    return str(request.args.get("workspace_id") or (request.get_json(silent=True) or {}).get("workspace_id") or "").strip()


def _resume_checkpoint(workspace_id: str, checkpoint: dict) -> None:
    """Continue a settled logical turn outside the approval HTTP request."""
    checkpoint_id = str(checkpoint.get("checkpoint_id") or "")
    try:
        from agent.app.service import get_default_agent_app
        from core.runtime_engine.models import ApprovalContinuationRuntimeControl
        result = get_default_agent_app().submit_user_message(
            user_input=str(checkpoint.get("user_input") or ""),
            workspace_id=workspace_id,
            session_id=str(checkpoint.get("session_id") or ""),
            metadata={},
            runtime_control=ApprovalContinuationRuntimeControl(
                checkpoint=checkpoint,
                workbench_context=dict(checkpoint.get("workbench_context") or {}),
            ),
        )
        service.settle_continuation(workspace_id, checkpoint_id, result=result.to_dict() if result else {})
    except Exception as exc:
        # Keep the durable checkpoint claimed but diagnosable; an operator can
        # retry a recovery endpoint later without ever replaying the device call.
        service.settle_continuation(workspace_id, checkpoint_id, result={
            "ok": False, "error": f"approval_resume_exception:{exc}",
        })


def _start_ready_continuation(workspace_id: str, operation_id: str) -> dict | None:
    checkpoint = service.claim_ready_continuation(workspace_id, operation_id)
    if checkpoint is None:
        return None
    _CONTINUATION_EXECUTOR.submit(_resume_checkpoint, workspace_id, checkpoint)
    return {"checkpoint_id": checkpoint.get("checkpoint_id"), "status": "resuming"}


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
                continuation = _start_ready_continuation(workspace_id, operation_id)
                return jsonify({"ok": True, "operation": record, "continuation": continuation})
            claimed = service.claim_execution(workspace_id, operation_id)
            if claimed.get("status") != "executing":
                continuation = _start_ready_continuation(workspace_id, operation_id)
                return jsonify({"ok": False, "operation": claimed, "error": "operation_invalidated", "continuation": continuation}), 409
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
                continuation = _start_ready_continuation(workspace_id, operation_id)
                return jsonify({"ok": False, "operation": settled, "error": "approval_execution_exception", "continuation": continuation})
            raw = result.output if isinstance(result.output, dict) else {}
            settled = service.settle_execution(workspace_id, operation_id, {
                **raw,
                "ok": result.status in {"succeeded", "dry_run"},
                "error": "; ".join(result.errors or []),
            })
            continuation = _start_ready_continuation(workspace_id, operation_id)
            return jsonify({"ok": True, "operation": settled, "continuation": continuation})
        except KeyError:
            return jsonify({"ok": False, "error": "operation_not_found"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400


def register():
    return {
        "register_routes": register_routes,
        "execution_interceptor": service.execution_interceptor,
    }
