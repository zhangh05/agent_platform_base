"""Public discovery endpoint for installed UI and runtime extensions."""

from flask import jsonify, request
from pathlib import Path
import tempfile

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

    @app.route("/api/extensions/<extension_id>/quota")
    def extension_quota_status(extension_id):
        from extensions.quota import quota_status
        from extensions.registry import ExtensionRegistry
        workspace_id = str(request.args.get("workspace_id") or "").strip()
        manifest = next((item for item in ExtensionRegistry().discover() if item.extension_id == extension_id), None)
        if not manifest:
            return jsonify({"ok": False, "error": "extension_not_found"}), 404
        if not workspace_id:
            return jsonify({"ok": False, "error": "workspace_id is required"}), 400
        return jsonify({"ok": True, "quota": quota_status(extension_id, workspace_id, manifest.metadata.get("quotas"))})

    @app.route("/api/extensions/repository")
    def extension_repository():
        from extensions.repository import list_packages
        packages = [{key: value for key, value in item.items() if key != "package_path"} for item in list_packages()]
        return jsonify({"ok": True, "packages": packages})

    @app.route("/api/extensions/repository/publish", methods=["POST"])
    def publish_extension_package():
        from extensions.package import ExtensionPackageError, MAX_PACKAGE_BYTES
        from extensions.repository import publish_package
        if request.content_length and request.content_length > MAX_PACKAGE_BYTES + 1_048_576:
            return jsonify({"ok": False, "error": "extension package is too large"}), 413
        uploaded = request.files.get("package")
        if uploaded is None or not uploaded.filename:
            return jsonify({"ok": False, "error": "extension package is required"}), 400
        with tempfile.TemporaryDirectory(prefix="extension-upload-") as directory:
            package_path = Path(directory) / "upload.apx"
            uploaded.save(package_path)
            try:
                record = publish_package(package_path)
            except ExtensionPackageError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
        public_record = {key: value for key, value in record.items() if key != "package_path"}
        return jsonify({"ok": True, "package": public_record}), 201

    @app.route("/api/extensions/repository/<extension_id>/<version>/install", methods=["POST"])
    def install_repository_extension(extension_id, version):
        from extensions.package import ExtensionPackageError, install_package
        from extensions.repository import get_package
        record = get_package(extension_id, version)
        if not record:
            return jsonify({"ok": False, "error": "extension_package_not_found"}), 404
        try:
            result = install_package(record["package_path"], upgrade=bool((request.get_json(silent=True) or {}).get("upgrade")))
        except ExtensionPackageError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({**result, "restart_required": True})

    @app.route("/api/extensions/<extension_id>/uninstall", methods=["POST"])
    def uninstall_plugin_extension(extension_id):
        from extensions.package import ExtensionPackageError, uninstall_extension
        try:
            result = uninstall_extension(extension_id)
        except ExtensionPackageError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({**result, "restart_required": True})

    register_extension_routes(app)
