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

    @app.route("/api/admin/approval-continuations")
    def approval_continuations_list():
        """Expose fail-closed continuation state without payloads or secrets."""
        from agent.runtime.approval_continuation import (
            list_continuations,
            maintain_continuations,
        )
        from storage.ids import validate_workspace_id

        try:
            workspace_id = validate_workspace_id(str(request.args.get("workspace_id") or ""))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        status = str(request.args.get("status") or "").strip()
        try:
            limit = max(1, min(int(request.args.get("limit") or 100), 500))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_limit"}), 400
        maintenance = maintain_continuations(workspace_id, force=True)
        all_records = list_continuations(workspace_id, limit=5000)
        records = [
            record for record in all_records
            if not status or str(record.get("status") or "") == status
        ][:limit]
        counts: dict[str, int] = {}
        for record in all_records:
            state = str(record.get("status") or "unknown")
            counts[state] = counts.get(state, 0) + 1
        return jsonify({
            "ok": True,
            "continuations": records,
            "count": len(records),
            "counts": counts,
            "maintenance": maintenance,
        })

    @app.route(
        "/api/admin/approval-continuations/<continuation_id>/close",
        methods=["POST"],
    )
    def approval_continuation_close(continuation_id):
        """Close a stalled unknown-outcome execution; never replay it."""
        from agent.runtime.approval_continuation import close_stalled_continuation
        from storage.ids import validate_workspace_id

        data = request.get_json(silent=True) or {}
        try:
            workspace_id = validate_workspace_id(str(data.get("workspace_id") or ""))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400
        confirmation = str(data.get("confirmation") or "")
        if confirmation != f"CLOSE {continuation_id}":
            return jsonify({
                "ok": False,
                "error": "confirmation_required",
                "expected": f"CLOSE {continuation_id}",
            }), 400
        reason = str(data.get("reason") or "").strip()
        if not reason:
            return jsonify({"ok": False, "error": "reason_required"}), 400
        try:
            record = close_stalled_continuation(
                workspace_id, continuation_id, reason=reason
            )
        except FileNotFoundError:
            return jsonify({"ok": False, "error": "continuation_not_found"}), 404
        except (RuntimeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        return jsonify({
            "ok": True,
            "continuation_id": continuation_id,
            "status": record.get("status"),
        })
