"""Public discovery endpoint for installed UI and runtime extensions."""

from flask import jsonify

from extensions.runtime import public_extension_catalog, register_extension_routes


def register_extensions(app) -> None:
    @app.route("/api/extensions")
    def list_extensions():
        catalog = public_extension_catalog()
        return jsonify({"ok": True, "extensions": catalog, "count": len(catalog)})

    register_extension_routes(app)
