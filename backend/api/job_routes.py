# backend/api/job_routes.py
"""Job API routes — job CRUD and worker management."""

from flask import jsonify, request
from storage.ids import validate_job_id, validate_workspace_id
from jobs.redaction import sanitize_job_record_for_api


def _invalid_ws():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _invalid_limit():
    return jsonify({"ok": False, "error": "invalid_limit"}), 400


def _validated_ws_id(raw="default"):
    try:
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_ws()


def _validated_limit(default=100, max_value=500):
    from backend.core.params import parse_limit
    try:
        return parse_limit(request.args, default=default, max_value=max_value), None
    except ValueError:
        return None, _invalid_limit()


def _validated_job_id(raw):
    try:
        return validate_job_id(raw), None
    except ValueError:
        return None, (jsonify({"ok": False, "error": "invalid_job_id"}), 400)


def register_job_routes(app):
    """Register all job API routes on the Flask app."""

    @app.route("/api/jobs", methods=["POST"])
    def api_job_create():
        data = request.get_json(silent=True) or {}
        workspace_id, err = _validated_ws_id(data.get("workspace_id", ""))
        if err:
            return err
        from jobs.manager import create_job
        try:
            rec = create_job(
                workspace_id=workspace_id,
                job_type=data.get("job_type", "agent_run"),
                title=data.get("title", ""),
                payload=data.get("payload", {}),
                input_artifacts=data.get("input_artifacts", []),
                enqueue=data.get("enqueue", True),
            )
            if data.get("run_immediately"):
                from jobs.runner import run_job
                run_job(rec.workspace_id, rec.job_id)
            return jsonify({"ok": True, "job": sanitize_job_record_for_api(rec.as_dict())})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/jobs")
    def api_jobs_list():
        ws = request.args.get("workspace_id", "")
        if not ws:
            return jsonify(
                {"ok": False, "error": "workspace_id is required"}
            ), 400
        ws, err = _validated_ws_id(ws)
        if err:
            return err
        status = request.args.get("status")
        jtype = request.args.get("job_type")
        lim, err = _validated_limit(default=100, max_value=500)
        if err:
            return err
        from jobs.store import list_jobs
        return jsonify({"jobs": list_jobs(ws_id=ws, status=status, job_type=jtype, limit=lim)})

    @app.route("/api/jobs/<job_id>", methods=["GET", "DELETE"])
    def api_job_detail(job_id):
        job_id, err = _validated_job_id(job_id)
        if err:
            return err
        data = (request.get_json(silent=True) or {}) if request.method == "DELETE" else {}
        ws = data.get("workspace_id", "") if request.method == "DELETE" else request.args.get("workspace_id", "")
        if not ws:
            return jsonify(
                {"ok": False, "error": "workspace_id is required for job lookup"}
            ), 400
        ws, err = _validated_ws_id(ws)
        if err:
            return err
        from jobs.store import get_job
        rec = get_job(ws, job_id)
        if not rec:
            return jsonify({"ok": False, "error": "job not found"}), 404
        if request.method == "DELETE":
            if str(data.get("confirmation") or "") != f"DELETE {job_id}":
                return jsonify({"ok": False, "error": "job_delete_confirmation_required"}), 400
            if rec.status in {"queued", "running"}:
                return jsonify({"ok": False, "error": "active_job_cannot_be_deleted"}), 409
            from jobs.store import delete_job
            delete_job(ws, job_id, soft=False)
            return jsonify({"ok": True, "job_id": job_id, "deleted": True})
        return jsonify({"ok": True, "job": sanitize_job_record_for_api(rec.as_dict())})

    @app.route("/api/workspaces/<ws_id>/jobs")
    def api_workspace_jobs(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from jobs.store import list_jobs
        return jsonify({"jobs": list_jobs(ws_id=ws_id)})

    @app.route("/api/workspaces/<ws_id>/jobs/<job_id>")
    def api_workspace_job_detail(ws_id, job_id):
        job_id, err = _validated_job_id(job_id)
        if err:
            return err
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from jobs.store import get_job
        rec = get_job(ws_id, job_id)
        if not rec:
            return jsonify({"ok": False, "error": "job not found"}), 404
        return jsonify({"ok": True, "job": sanitize_job_record_for_api(rec.as_dict())})

    @app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
    def api_job_cancel(job_id):
        job_id, err = _validated_job_id(job_id)
        if err:
            return err
        data = request.get_json(silent=True) or {}
        ws, err = _validated_ws_id(data.get("workspace_id", ""))
        if err:
            return err
        expected_client_request_id = str(data.get("client_request_id") or "").strip()
        from jobs.manager import cancel_job
        try:
            rec = cancel_job(
                ws,
                job_id,
                expected_client_request_id=expected_client_request_id,
            )
            try:
                from backend.ws.agent_ws import request_active_turn_cancel
                from storage.principal import current_storage_principal
                request_active_turn_cancel(current_storage_principal(), ws, job_id)
            except Exception:
                # The durable cancel_requested flag remains authoritative even
                # when the worker is in another process or already terminal.
                pass
            return jsonify({"ok": True, "job": sanitize_job_record_for_api(rec.as_dict())})
        except ValueError as e:
            if str(e) == "stale_turn":
                return jsonify({"ok": False, "error": "stale_turn"}), 409
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/jobs/<job_id>/retry", methods=["POST"])
    def api_job_retry(job_id):
        job_id, err = _validated_job_id(job_id)
        if err:
            return err
        ws, err = _validated_ws_id((request.get_json(silent=True) or {}).get("workspace_id", ""))
        if err:
            return err
        from jobs.manager import retry_job
        try:
            rec = retry_job(ws, job_id)
            return jsonify({"ok": True, "job": sanitize_job_record_for_api(rec.as_dict())})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/jobs/<job_id>/events")
    def api_job_events(job_id):
        job_id, err = _validated_job_id(job_id)
        if err:
            return err
        ws, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        from jobs.store import list_events
        return jsonify({"events": list_events(ws, job_id)})

    @app.route("/api/jobs/<job_id>/logs")
    def api_job_logs(job_id):
        job_id, err = _validated_job_id(job_id)
        if err:
            return err
        ws, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        from jobs.store import list_logs
        return jsonify({"logs": list_logs(ws, job_id)})

    @app.route("/api/jobs/<job_id>/artifacts")
    def api_job_artifacts(job_id):
        job_id, err = _validated_job_id(job_id)
        if err:
            return err
        ws, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        from jobs.store import get_job
        rec = get_job(ws, job_id)
        if not rec:
            return jsonify({"ok": False, "error": "job not found"}), 404

        input_arts = list(rec.input_artifacts or [])
        output_arts = list(rec.output_artifacts or [])
        report_arts = list(rec.report_artifacts or [])

        # Also aggregate from per-run artifact indexes
        try:
            from artifacts.store import get_run_artifacts, list_artifacts, get_artifact
            for rid in (rec.run_ids or []):
                ra = get_run_artifacts(ws, rid)
                for a in ra.get("input_artifacts", []):
                    art_id = a.get("artifact_id") if isinstance(a, dict) else a
                    if art_id and art_id not in input_arts:
                        input_arts.append(art_id)
                for a in ra.get("output_artifacts", []):
                    art_id = a.get("artifact_id") if isinstance(a, dict) else a
                    if art_id and art_id not in output_arts:
                        output_arts.append(art_id)
                for a in ra.get("report_artifacts", []):
                    art_id = a.get("artifact_id") if isinstance(a, dict) else a
                    if art_id and art_id not in report_arts:
                        report_arts.append(art_id)
                for a in ra.get("temp_artifacts", []):
                    art_id = a.get("artifact_id") if isinstance(a, dict) else a
                    if art_id and art_id not in output_arts:
                        output_arts.append(art_id)

            # Some produced artifacts carry session_id so we can filter
            # by session instead of globally scanning all indexes.
            session_id = (rec.payload or {}).get("session_id", "")
            if session_id:
                for art_summary in list_artifacts(ws, include_deleted=False, limit=500):
                    art_id = art_summary.get("artifact_id")
                    if not art_id or art_id in output_arts:
                        continue
                    art_rec = get_artifact(ws, art_id)
                    if art_rec and art_rec.session_id == session_id:
                        output_arts.append(art_id)
        except Exception:
            pass

        return jsonify({"input_artifacts": input_arts,
                        "output_artifacts": output_arts,
                        "report_artifacts": report_arts})

    @app.route("/api/jobs/worker/run-once", methods=["POST"])
    def api_worker_run_once():
        from jobs.worker import run_once
        result = run_once()
        return jsonify({"ok": True, **result})

    @app.route("/api/jobs/worker/status")
    def api_worker_status():
        from jobs.worker import get_worker_state
        return jsonify(get_worker_state())
