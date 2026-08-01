"""Administrative production-readiness and backup operations."""

from flask import jsonify, request


def register_admin_routes(app) -> None:
    @app.route("/api/admin/production")
    def production_status():
        from core.runtime.production import production_readiness
        return jsonify(production_readiness())

    @app.route("/api/admin/backups")
    def backups_list():
        from core.runtime.backup import list_backups
        return jsonify({"ok": True, "backups": list_backups()})

    @app.route("/api/admin/backups", methods=["POST"])
    def backups_create():
        from core.runtime.backup import BackupError, create_backup
        try:
            result = create_backup()
        except BackupError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        public = {key: value for key, value in result.items() if key != "path"}
        return jsonify({"ok": True, "backup": public}), 201

    @app.route("/api/admin/backups/prune", methods=["POST"])
    def backups_prune():
        from core.runtime.backup import prune_backups
        try:
            keep = int((request.get_json(silent=True) or {}).get("keep") or 10)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "keep must be an integer"}), 400
        return jsonify({"ok": True, "removed": prune_backups(keep)})

    @app.route("/api/admin/backups/<backup_id>/restore", methods=["POST"])
    def backups_restore(backup_id):
        from core.runtime.backup import BackupError, backup_path, restore_backup
        confirmation = str((request.get_json(silent=True) or {}).get("confirmation") or "")
        try:
            result = restore_backup(backup_path(backup_id), confirmation=confirmation)
        except BackupError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        return jsonify(result)
