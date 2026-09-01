"""Workspace workflow definition, template and execution APIs."""
from flask import jsonify, request, session


def _workspace_id(value) -> str:
    from storage.ids import validate_workspace_id
    try:
        return validate_workspace_id(str(value or ""))
    except ValueError:
        return ""


def register_workflow_routes(app) -> None:
    @app.route("/api/workflow-templates")
    def workflow_templates():
        from workflows.templates import list_workflow_templates
        return jsonify({"ok": True, "templates": list_workflow_templates()})

    @app.route("/api/workflow-templates/<template_id>/instantiate", methods=["POST"])
    def instantiate_workflow_template(template_id):
        from workflows.service import WorkflowError
        from workflows.templates import instantiate_workflow_template
        data = request.get_json(silent=True) or {}
        workspace_id = _workspace_id(data.get("workspace_id"))
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            result = instantiate_workflow_template(workspace_id, template_id, name=str(data.get("name") or ""))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except WorkflowError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, **result}), 201

    @app.route("/api/workflows", methods=["GET", "POST"])
    def workflows_collection():
        from workflows.service import WorkflowError, list_workflows, save_workflow
        data = request.get_json(silent=True) or {}
        workspace_id = _workspace_id(request.args.get("workspace_id") or data.get("workspace_id"))
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            return jsonify({"ok": True, "workflows": list_workflows(workspace_id)})
        try:
            workflow = save_workflow(workspace_id, data)
        except WorkflowError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "workflow": workflow}), 201

    @app.route("/api/workflows/<workflow_id>", methods=["GET", "PUT", "DELETE"])
    def workflow_detail(workflow_id):
        from workflows.service import WorkflowError, delete_workflow, get_workflow, save_workflow
        data = request.get_json(silent=True) or {}
        workspace_id = _workspace_id(request.args.get("workspace_id") or data.get("workspace_id"))
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            workflow = get_workflow(workspace_id, workflow_id)
            return (jsonify({"ok": True, "workflow": workflow}), 200) if workflow else (jsonify({"ok": False, "error": "workflow not found"}), 404)
        if request.method == "DELETE":
            if data.get("confirm") != "delete":
                return jsonify({"ok": False, "error": "workflow_delete_confirmation_required"}), 400
            try:
                deleted = delete_workflow(workspace_id, workflow_id)
            except WorkflowError as exc:
                status = 409 if str(exc) == "workflow_has_active_runs" else 400
                return jsonify({"ok": False, "error": str(exc)}), status
            return jsonify({"ok": True, "deleted": deleted})
        try:
            workflow = save_workflow(workspace_id, {**data, "workflow_id": workflow_id})
        except WorkflowError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "workflow": workflow})

    @app.route("/api/workflows/<workflow_id>/runs", methods=["GET", "POST"])
    def workflow_runs(workflow_id):
        from workflows.service import WorkflowError, execute_workflow, list_runs, validate_workflow_inputs
        data = request.get_json(silent=True) or {}
        workspace_id = _workspace_id(request.args.get("workspace_id") or data.get("workspace_id"))
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        if request.method == "GET":
            return jsonify({"ok": True, "runs": list_runs(workspace_id, workflow_id)})
        try:
            inputs = validate_workflow_inputs(data.get("inputs") or {})
        except WorkflowError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if data.get("enqueue"):
            from jobs.manager import create_job
            from jobs.redaction import sanitize_job_record_for_api
            job = create_job(workspace_id, "workflow_run", f"Workflow: {workflow_id}", {"workflow_id": workflow_id, "inputs": inputs})
            return jsonify({"ok": True, "queued": True, "job": sanitize_job_record_for_api(job.as_dict())}), 202
        try:
            run = execute_workflow(workspace_id, workflow_id, inputs)
        except WorkflowError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": run["status"] == "succeeded", "run": run}), 200 if run["status"] == "succeeded" else 409

    @app.route("/api/workflow-runs/<run_id>")
    def workflow_run_detail(run_id):
        from workflows.service import get_run
        workspace_id = _workspace_id(request.args.get("workspace_id"))
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        run = get_run(workspace_id, run_id)
        return (jsonify({"ok": True, "run": run}), 200) if run else (jsonify({"ok": False, "error": "workflow not found"}), 404)

    @app.route("/api/workflow-runs/<run_id>/cancel", methods=["POST"])
    def workflow_run_cancel(run_id):
        from workflows.service import WorkflowError, cancel_run
        workspace_id = _workspace_id((request.get_json(silent=True) or {}).get("workspace_id"))
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        try:
            run = cancel_run(workspace_id, run_id)
        except WorkflowError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        return jsonify({"ok": True, "run": run})
