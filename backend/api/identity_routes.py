"""Organization, membership, and user administration endpoints."""

import re

from flask import jsonify, request, session

from backend.core.identity import (
    create_organization,
    ensure_organization,
    get_user,
    has_role,
    identity_enabled,
    list_memberships,
    list_organizations,
    list_users,
    upsert_membership,
    upsert_user,
    update_user_access,
)


def _context():
    if not identity_enabled():
        return None, (jsonify({"ok": False, "error": "identity_disabled"}), 404)
    role = str(session.get("agent_platform_role") or "")
    if not has_role(role, "admin"):
        return None, (jsonify({"ok": False, "error": "forbidden"}), 403)
    username = str(session.get("agent_platform_user") or "")
    user = get_user(username)
    platform_admin = user is None or role == "owner"
    if platform_admin:
        from storage.workspace_store import list_workspace_ids
        ensure_organization("default", "默认组织", list_workspace_ids())
    return {
        "username": username,
        "role": role,
        "organization_id": str(session.get("agent_platform_org") or "default"),
        "platform_admin": platform_admin,
    }, None


def _allowed_org(context: dict, organization_id: str) -> bool:
    return bool(context["platform_admin"] or organization_id == context["organization_id"])


def _validated_user_fields(
    username: str,
    password: str,
    organization_id: str,
    workspace_ids,
    *,
    require_password: bool = True,
) -> tuple[list[str] | None, str | None]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
        return None, "invalid username"
    if require_password and not password:
        return None, "password is required"
    if not isinstance(workspace_ids, list):
        return None, "workspace_ids must be a list"
    from storage.ids import validate_workspace_id
    try:
        selected = sorted({validate_workspace_id(item) for item in workspace_ids})
    except ValueError:
        return None, "invalid workspace_id"
    organization = next((item for item in list_organizations() if item.get("organization_id") == organization_id), None)
    if organization is None or not set(selected).issubset(set(organization.get("workspace_ids") or [])):
        return None, "workspace is not assigned to the organization"
    if not selected:
        return None, "at least one workspace is required"
    return selected, None


def register_identity_routes(app) -> None:
    @app.route("/api/identity/users", methods=["GET", "POST"])
    def identity_users():
        context, denied = _context()
        if denied: return denied
        if not context["platform_admin"]:
            return jsonify({"ok": False, "error": "platform_admin_required"}), 403
        if request.method == "GET":
            return jsonify({"ok": True, "users": list_users()})
        data = request.get_json(silent=True) or {}
        organization_id = str(data.get("organization_id") or context["organization_id"])
        if not _allowed_org(context, organization_id):
            return jsonify({"ok": False, "error": "organization_forbidden"}), 403
        existing = get_user(str(data.get("username", "")))
        if existing and not context["platform_admin"] and existing.get("organization_id") != organization_id:
            return jsonify({"ok": False, "error": "user_belongs_to_another_organization"}), 403
        if str(data.get("role") or "viewer") not in {"viewer", "operator", "developer"}:
            return jsonify({"ok": False, "error": "ordinary_user_role_required"}), 400
        from backend.core.auth import _get_login_username
        username = str(data.get("username") or "").strip()
        if username.casefold() == _get_login_username().casefold():
            return jsonify({"ok": False, "error": "administrator_account_is_protected"}), 400
        workspace_ids, validation_error = _validated_user_fields(username, str(data.get("password") or ""), organization_id, data.get("workspace_ids"))
        if validation_error:
            return jsonify({"ok": False, "error": validation_error}), 400
        try:
            user = upsert_user(
                username,
                str(data.get("password", "")),
                str(data.get("role", "viewer")),
                organization_id,
                workspace_ids,
                home_workspace_id=workspace_ids[0],
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "user": user}), 201

    @app.route("/api/identity/users/<username>", methods=["PUT"])
    def identity_user_update(username):
        context, denied = _context()
        if denied: return denied
        if not context["platform_admin"]:
            return jsonify({"ok": False, "error": "platform_admin_required"}), 403
        from backend.core.auth import _get_login_username
        if str(username).casefold() == _get_login_username().casefold():
            return jsonify({"ok": False, "error": "administrator_account_is_protected"}), 400
        data = request.get_json(silent=True) or {}
        role = str(data.get("role") or "viewer")
        if role not in {"viewer", "operator", "developer"}:
            return jsonify({"ok": False, "error": "ordinary_user_role_required"}), 400
        workspace_ids, validation_error = _validated_user_fields(
            str(username),
            str(data.get("password") or ""),
            str(data.get("organization_id") or "default"),
            data.get("workspace_ids") or [],
            require_password=False,
        )
        if validation_error:
            return jsonify({"ok": False, "error": validation_error}), 400
        try:
            user = update_user_access(
                username,
                role,
                str(data.get("organization_id") or "default"),
                workspace_ids,
                enabled=data.get("enabled", True) is not False,
                password=str(data.get("password") or ""),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "user": user})

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
        if not context["platform_admin"]:
            return jsonify({"ok": False, "error": "platform_admin_required"}), 403
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
        if str(data.get("role") or "viewer") not in {"viewer", "operator", "developer"}:
            return jsonify({"ok": False, "error": "ordinary_user_role_required"}), 400
        try:
            membership = upsert_membership(organization_id, str(data.get("username") or ""), str(data.get("role") or "viewer"), data.get("workspace_ids"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "membership": membership}), 201
