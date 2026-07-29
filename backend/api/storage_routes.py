"""Unified managed-file metadata and event APIs."""

from __future__ import annotations

from flask import Response, jsonify, request, stream_with_context

from storage.ids import validate_workspace_id


def register_storage_routes(app) -> None:
    @app.route("/api/storage/overview")
    def api_storage_overview():
        try:
            workspace_id = validate_workspace_id(request.args.get("workspace_id", ""))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        from storage.data_management import data_overview
        return jsonify({"ok": True, "overview": data_overview(workspace_id)})

    @app.route("/api/storage/files")
    def api_storage_files():
        try:
            workspace_id = validate_workspace_id(request.args.get("workspace_id", ""))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        logical_type = request.args.get("logical_type", "").strip()
        lifecycle = request.args.get("lifecycle", "active").strip()
        from storage.data_management import managed_data_files
        files = managed_data_files(workspace_id, logical_type=logical_type, lifecycle=lifecycle)
        return jsonify({"ok": True, "files": files, "count": len(files)})

    @app.route("/api/storage/files/<file_id>/relations")
    def api_storage_file_relations(file_id):
        try:
            workspace_id = validate_workspace_id(request.args.get("workspace_id", ""))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        from storage.data_management import file_relations
        relations = file_relations(workspace_id, file_id)
        if relations is None:
            return jsonify({"ok": False, "error": "file_not_found"}), 404
        return jsonify({"ok": True, "relations": relations})

    @app.route("/api/storage/files/<file_id>/content")
    def api_storage_file_content(file_id):
        try:
            workspace_id = validate_workspace_id(request.args.get("workspace_id", ""))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        from storage.data_management import text_file_content
        try:
            content = text_file_content(workspace_id, file_id)
        except (OSError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)[:160]}), 400
        if content is None:
            return jsonify({"ok": False, "error": "file_not_found"}), 404
        return jsonify({"ok": True, **content})

    @app.route("/api/storage/files/<file_id>", methods=["DELETE"])
    def api_storage_file_delete(file_id):
        try:
            workspace_id = validate_workspace_id(request.args.get("workspace_id", ""))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        confirmed = request.args.get("confirm", "").lower() == "true"
        if not confirmed:
            return jsonify({"ok": False, "error": "confirm_required"}), 400
        force = request.args.get("force", "").lower() == "true"
        from storage.data_management import delete_unreferenced_file
        result = delete_unreferenced_file(workspace_id, file_id, force=force)
        if result.get("ok"):
            return jsonify(result)
        status = 409 if result.get("error") == "file_in_use" else 404 if result.get("error") == "file_not_found" else 400
        return jsonify(result), status

    @app.route("/api/storage/events")
    def api_storage_events():
        try:
            workspace_id = validate_workspace_id(request.args.get("workspace_id", ""))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400

        def generate():
            import queue
            from storage.events import subscribe
            with subscribe(workspace_id) as subscriber:
                yield "event: connected\ndata: {}\n\n"
                while True:
                    try:
                        yield f"event: storage_changed\ndata: {subscriber.get(timeout=25)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
