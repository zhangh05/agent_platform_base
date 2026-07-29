# backend/api/state_routes.py
"""Runtime State API — Phase 2 endpoints.

GET  /api/runtime/tasks?workspace_id=&session_id=
GET  /api/runtime/tasks/<task_id>?workspace_id=
GET  /api/runtime/tasks/<task_id>/events?workspace_id=
GET  /api/runtime/tasks/<task_id>/checkpoints?workspace_id=
"""

from __future__ import annotations


def register_state_routes(app):
    """Register runtime state API routes on the Flask app."""

    def _validated_ws_id(raw: str) -> tuple[str, tuple | None]:
        from flask import jsonify
        if not raw:
            return "", (jsonify({"ok": False, "error": "workspace_id is required"}), 400)
        try:
            from storage.ids import validate_workspace_id
            return validate_workspace_id(raw), None
        except Exception:
            return "", (jsonify({"ok": False, "error": "invalid_workspace_id"}), 400)

    @app.route("/api/runtime/tasks")
    def api_runtime_tasks():
        from flask import request, jsonify
        ws_id = request.args.get("workspace_id", "")
        session_id = request.args.get("session_id", "")

        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err

        try:
            from agent.runtime.durable.store import list_tasks
            tasks = list_tasks(ws_id, session_id=session_id)
            return jsonify({
                "ok": True,
                "tasks": [t.to_dict() for t in tasks],
                "count": len(tasks),
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/runtime/tasks/<task_id>")
    def api_runtime_task_detail(task_id):
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err

        try:
            from agent.runtime.durable.store import get_task
            task = get_task(ws_id, task_id)
            if not task:
                return jsonify({"ok": False, "error": "task not found"}), 404
            # Ensure boundary: task must belong to this workspace
            if task.workspace_id != ws_id:
                return jsonify({"ok": False, "error": "task not found in workspace"}), 404
            return jsonify({"ok": True, "task": task.to_dict()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/runtime/tasks/<task_id>/events")
    def api_runtime_task_events(task_id):
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err

        try:
            from agent.runtime.durable.store import get_task, get_events
            task = get_task(ws_id, task_id)
            if not task or task.workspace_id != ws_id:
                return jsonify({"ok": False, "error": "task not found"}), 404
            events = get_events(ws_id, task_id)
            return jsonify({"ok": True, "events": events, "count": len(events)})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/runtime/tasks/<task_id>/checkpoints")
    def api_runtime_task_checkpoints(task_id):
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err

        try:
            from agent.runtime.durable.store import get_task, get_checkpoints
            task = get_task(ws_id, task_id)
            if not task or task.workspace_id != ws_id:
                return jsonify({"ok": False, "error": "task not found"}), 404
            checkpoints = get_checkpoints(ws_id, task_id)
            return jsonify({"ok": True, "checkpoints": checkpoints, "count": len(checkpoints)})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    # ── Phase 3: Control endpoints (POST) ──

    @app.route("/api/runtime/tasks/<task_id>/checkpoint", methods=["POST"])
    def api_task_checkpoint(task_id):
        from flask import request, jsonify
        ws_id = (request.args.get("workspace_id", "") or
                 (request.get_json(silent=True) or {}).get("workspace_id", ""))
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        try:
            from agent.runtime.durable.control import checkpoint_task
            reason = (request.get_json(silent=True) or {}).get("reason", "manual")
            cp = checkpoint_task(task_id, ws_id, reason=reason)
            if not cp:
                return jsonify({"ok": False, "error": "task not found"}), 404
            return jsonify({"ok": True, "checkpoint_id": cp.checkpoint_id})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/runtime/tasks/<task_id>/cancel", methods=["POST"])
    def api_task_cancel(task_id):
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from agent.runtime.durable.store import get_task
            task = get_task(ws_id, task_id)
            if not task or task.workspace_id != ws_id:
                return jsonify({"ok": False, "error": "task not found in workspace"}), 404
            from agent.runtime.durable.control import cancel_task
            result = cancel_task(task_id, ws_id)
            code = 200 if result["ok"] else (404 if "not found" in result.get("error","") else 400)
            return jsonify(result), code
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/runtime/tasks/<task_id>/resume", methods=["POST"])
    def api_task_resume(task_id):
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from agent.runtime.durable.store import get_task
            task = get_task(ws_id, task_id)
            if not task or task.workspace_id != ws_id:
                return jsonify({"ok": False, "error": "task not found in workspace"}), 404
            from agent.runtime.durable.control import resume_task
            result = resume_task(task_id, ws_id)
            code = 200 if result["ok"] else (404 if "not found" in result.get("error","") else 400)
            return jsonify(result), code
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/runtime/tasks/<task_id>/steps/<step_id>/retry", methods=["POST"])
    def api_task_retry_step(task_id, step_id):
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from agent.runtime.durable.store import get_task
            task = get_task(ws_id, task_id)
            if not task or task.workspace_id != ws_id:
                return jsonify({"ok": False, "error": "task not found in workspace"}), 404
            from agent.runtime.durable.control import retry_step
            result = retry_step(task_id, step_id, ws_id)
            code = 200 if result["ok"] else (404 if "not found" in result.get("error","") else 400)
            return jsonify(result), code
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    # ── Phase 10: Trajectory endpoints ──

    @app.route("/api/runtime/trajectories")
    def api_trajectories():
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from agent.runtime.durable.trajectory import list_trajectories
            items = list_trajectories(ws_id)
            return jsonify({"ok": True, "trajectories": items, "count": len(items)})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/runtime/trajectories/<traj_id>")
    def api_trajectory_detail(traj_id):
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from agent.runtime.durable.trajectory import get_trajectory
            traj = get_trajectory(traj_id, ws_id)
            if not traj:
                return jsonify({"ok": False, "error": "trajectory not found"}), 404
            return jsonify({"ok": True, "trajectory": traj})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    # ── Phase 11: Ecosystem endpoints ──

    @app.route("/api/ecosystem/providers")
    def api_ecosystem_providers():
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from core.tools.ecosystem import EcoRegistry
            reg = EcoRegistry()
            provs = [p.to_dict() for p in reg.list_providers(ws_id)]
            return jsonify({"ok": True, "providers": provs, "count": len(provs)})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/ecosystem/import/preview", methods=["POST"])
    def api_ecosystem_import_preview():
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from core.tools.ecosystem import preview_import
            data = request.get_json(silent=True) or {}
            result = preview_import(data)
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/ecosystem/import/apply", methods=["POST"])
    def api_ecosystem_import_apply():
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from core.tools.ecosystem import apply_import
            data = request.get_json(silent=True) or {}
            confirm = str(request.args.get("confirm", data.get("confirm", "false"))).lower() == "true"
            result = apply_import(data, ws_id, confirm=confirm)
            code = 200 if result.get("ok") else 400
            return jsonify(result), code
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    # ── Phase 12: Audit report ──

    @app.route("/api/runtime/tasks/<task_id>/audit-report", methods=["POST"])
    def api_audit_report_generate(task_id):
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from agent.runtime.durable.delivery import build_audit_report
            report = build_audit_report(task_id, ws_id)
            return jsonify({"ok": True, "report": report})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500

    @app.route("/api/runtime/tasks/<task_id>/audit-report", methods=["GET"])
    def api_audit_report_get(task_id):
        from flask import request, jsonify
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        try:
            from agent.runtime.durable.delivery import export_audit_report_markdown
            md = export_audit_report_markdown(task_id, ws_id)
            return jsonify({"ok": True, "markdown": md})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:200]}), 500
