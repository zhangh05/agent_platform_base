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

    @app.route("/api/admin/operation-ledger")
    def operation_ledger_list():
        """List redacted durable write-operation facts; this endpoint never replays work."""
        from core.runtime_engine.operation_ledger import (
            list_operations,
            operation_counts,
            reconcile_operations,
        )
        from storage.ids import validate_workspace_id

        try:
            workspace_id = validate_workspace_id(str(request.args.get("workspace_id") or ""))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        status = str(request.args.get("status") or "").strip()
        allowed_statuses = {"planned", "running", "succeeded", "failed", "unknown", "blocked"}
        if status and status not in allowed_statuses:
            return jsonify({"ok": False, "error": "invalid_status"}), 400
        try:
            limit = max(1, min(int(request.args.get("limit") or 100), 500))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_limit"}), 400
        maintenance = reconcile_operations(workspace_id)
        records = list_operations(workspace_id, status=status, limit=limit)
        counts = operation_counts(workspace_id)
        return jsonify({
            "ok": True,
            "operations": records,
            "count": len(records),
            "counts": counts,
            "maintenance": maintenance,
        })

    @app.route("/api/admin/operation-ledger/<operation_id>/resolve", methods=["POST"])
    def operation_ledger_resolve(operation_id):
        """Resolve an uncertain operation from explicit human verification."""
        from core.runtime_engine.operation_ledger import resolve_operation_manually
        from storage.ids import validate_workspace_id

        data = request.get_json(silent=True) or {}
        try:
            workspace_id = validate_workspace_id(str(data.get("workspace_id") or ""))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        confirmation = str(data.get("confirmation") or "")
        if confirmation != f"RESOLVE {operation_id}":
            return jsonify({
                "ok": False,
                "error": "confirmation_required",
                "expected": f"RESOLVE {operation_id}",
            }), 400
        status = str(data.get("status") or "")
        if status not in {"succeeded", "failed"}:
            return jsonify({"ok": False, "error": "invalid_resolution_status"}), 400
        try:
            record = resolve_operation_manually(
                workspace_id,
                operation_id,
                status=status,
                reason=str(data.get("reason") or ""),
            )
        except FileNotFoundError:
            return jsonify({"ok": False, "error": "operation_not_found"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        return jsonify({
            "ok": True,
            "operation_id": operation_id,
            "status": record.get("status"),
            "resolved_by": record.get("resolved_by"),
        })
