"""Public discovery endpoint for installed UI and runtime extensions."""

from flask import jsonify, request

from extensions.runtime import public_extension_catalog, register_extension_routes


def register_extensions(app) -> None:
    @app.route("/api/extensions")
    def list_extensions():
        catalog = public_extension_catalog()
        return jsonify({"ok": True, "extensions": catalog, "count": len(catalog)})

    @app.route("/api/extensions/<extension_id>/enable", methods=["POST"])
    def enable_extension(extension_id):
        from extensions.registry import ExtensionRegistry
        from extensions.runtime import reset_extension_cache_for_tests
        from extensions.state import set_extension_enabled
        known = {item.extension_id for item in ExtensionRegistry().discover()}
        if extension_id not in known:
            return jsonify({"ok": False, "error": "extension_not_found"}), 404
        state = set_extension_enabled(extension_id, True)
        reset_extension_cache_for_tests()
        return jsonify({"ok": True, "lifecycle": state, "restart_required": True})

    @app.route("/api/extensions/<extension_id>/disable", methods=["POST"])
    def disable_extension(extension_id):
        from extensions.registry import ExtensionRegistry
        from extensions.runtime import reset_extension_cache_for_tests
        from extensions.state import set_extension_enabled
        known = {item.extension_id for item in ExtensionRegistry().discover()}
        if extension_id not in known:
            return jsonify({"ok": False, "error": "extension_not_found"}), 404
        state = set_extension_enabled(extension_id, False)
        reset_extension_cache_for_tests()
        return jsonify({"ok": True, "lifecycle": state, "restart_required": True})

    @app.route("/api/extensions/<extension_id>/migrate", methods=["POST"])
    def migrate_extension(extension_id):
        from extensions.runtime import load_extensions
        from extensions.sdk import run_migrations
        workspace_id = str((request.get_json(silent=True) or {}).get("workspace_id") or "").strip()
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        loaded = next((item for item in load_extensions() if item.manifest.extension_id == extension_id), None)
        if not loaded:
            return jsonify({"ok": False, "error": "extension_not_enabled"}), 409
        version = run_migrations(extension_id, workspace_id, list(loaded.migrations))
        return jsonify({"ok": True, "extension_id": extension_id, "workspace_id": workspace_id, "schema_version": version})

    register_extension_routes(app)
