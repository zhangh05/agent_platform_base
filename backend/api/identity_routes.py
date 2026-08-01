"""Organization, membership, and user administration endpoints."""

from flask import jsonify, request, session

from backend.core.identity import (
    create_organization,
    get_user,
    has_role,
    identity_enabled,
    list_memberships,
    list_organizations,
    list_users,
    upsert_membership,
    upsert_user,
)


def _context():
    if not identity_enabled():
        return None, (jsonify({"ok": False, "error": "identity_disabled"}), 404)
    role = str(session.get("agent_platform_role") or "")
    if not has_role(role, "admin"):
        return None, (jsonify({"ok": False, "error": "forbidden"}), 403)
    username = str(session.get("agent_platform_user") or "")
    user = get_user(username)
    return {
        "username": username,
        "role": role,
        "organization_id": str(session.get("agent_platform_org") or "default"),
        "platform_admin": user is None or role == "owner",
    }, None


def _allowed_org(context: dict, organization_id: str) -> bool:
    return bool(context["platform_admin"] or organization_id == context["organization_id"])


def register_identity_routes(app) -> None:
    @app.route("/api/identity/users", methods=["GET", "POST"])
    def identity_users():
        context, denied = _context()
        if denied: return denied
        if request.method == "GET":
            users = list_users()
            if not context["platform_admin"]:
                users = [item for item in users if item.get("organization_id") == context["organization_id"]]
            return jsonify({"ok": True, "users": users})
        data = request.get_json(silent=True) or {}
        organization_id = str(data.get("organization_id") or context["organization_id"])
        if not _allowed_org(context, organization_id):
            return jsonify({"ok": False, "error": "organization_forbidden"}), 403
        existing = get_user(str(data.get("username", "")))
        if existing and not context["platform_admin"] and existing.get("organization_id") != organization_id:
            return jsonify({"ok": False, "error": "user_belongs_to_another_organization"}), 403
        if str(data.get("role") or "viewer") == "owner" and not context["platform_admin"]:
            return jsonify({"ok": False, "error": "platform_admin_required"}), 403
        try:
            user = upsert_user(str(data.get("username", "")), str(data.get("password", "")), str(data.get("role", "viewer")), organization_id, data.get("workspace_ids"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "user": user}), 201

    @app.route("/api/identity/organizations", methods=["GET", "POST"])
    def identity_organizations():
        context, denied = _context()
        if denied: return denied
        if request.method == "GET":
            organizations = list_organizations()
            if not context["platform_admin"]:
                organizations = [item for item in organizations if item.get("organization_id") == context["organization_id"]]
            return jsonify({"ok": True, "organizations": organizations})
        if not context["platform_admin"]:
            return jsonify({"ok": False, "error": "platform_admin_required"}), 403
        data = request.get_json(silent=True) or {}
        try:
            organization = create_organization(str(data.get("organization_id") or ""), str(data.get("name") or ""))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "organization": organization}), 201

    @app.route("/api/identity/organizations/<organization_id>/memberships", methods=["GET", "POST"])
    def identity_memberships(organization_id):
        context, denied = _context()
        if denied: return denied
        if not _allowed_org(context, organization_id):
            return jsonify({"ok": False, "error": "organization_forbidden"}), 403
        if request.method == "GET":
            try:
                memberships = list_memberships(organization_id)
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            return jsonify({"ok": True, "memberships": memberships})
        data = request.get_json(silent=True) or {}
        existing = get_user(str(data.get("username") or ""))
        if existing and not context["platform_admin"] and existing.get("organization_id") != organization_id:
            return jsonify({"ok": False, "error": "user_belongs_to_another_organization"}), 403
        if str(data.get("role") or "viewer") == "owner" and not context["platform_admin"]:
            return jsonify({"ok": False, "error": "platform_admin_required"}), 403
        try:
            membership = upsert_membership(organization_id, str(data.get("username") or ""), str(data.get("role") or "viewer"), data.get("workspace_ids"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "membership": membership}), 201
