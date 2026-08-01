"""Identity administration endpoints, enabled only in identity mode."""

from flask import jsonify, request, session

from backend.core.identity import has_role, identity_enabled, list_users, upsert_user


def _admin_only():
    if not identity_enabled():
        return jsonify({"ok": False, "error": "identity_disabled"}), 404
    role = session.get("agent_platform_role", "")
    if not has_role(role, "admin"):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    return None


def handle_identity_users():
    denied = _admin_only()
    if denied:
        return denied
    if request.method == "GET":
        return jsonify({"ok": True, "users": list_users()})
    data = request.get_json(silent=True) or {}
    try:
        user = upsert_user(str(data.get("username", "")), str(data.get("password", "")), str(data.get("role", "viewer")), str(data.get("organization_id", "default")), data.get("workspace_ids"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "user": user}), 201
