"""Optional OpenID Connect login backed by pre-provisioned platform users."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from flask import jsonify, redirect, session


_LOCAL_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,63}$")


def oidc_enabled() -> bool:
    return os.environ.get("AGENT_PLATFORM_OIDC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _public_url() -> str:
    value = os.environ.get("AGENT_PLATFORM_PUBLIC_URL", "").strip().rstrip("/")
    parsed = urlparse(value)
    if not value or parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("AGENT_PLATFORM_PUBLIC_URL must be an absolute HTTPS URL for OIDC")
    return value


def _username_from_claims(claims: dict) -> str:
    claim_name = os.environ.get("AGENT_PLATFORM_OIDC_USERNAME_CLAIM", "preferred_username").strip()
    value = str(claims.get(claim_name) or claims.get("email") or claims.get("sub") or "").strip()
    if not _LOCAL_USERNAME_RE.fullmatch(value):
        raise ValueError("OIDC token does not contain a usable username claim")
    return value


def register_oidc_routes(app) -> None:
    if not oidc_enabled():
        @app.route("/api/auth/oidc/start")
        def oidc_disabled_start():
            return jsonify({"ok": False, "error": "oidc_disabled"}), 404

        @app.route("/api/auth/oidc/callback")
        def oidc_disabled_callback():
            return jsonify({"ok": False, "error": "oidc_disabled"}), 404
        return

    issuer = os.environ.get("AGENT_PLATFORM_OIDC_ISSUER", "").strip().rstrip("/")
    client_id = os.environ.get("AGENT_PLATFORM_OIDC_CLIENT_ID", "").strip()
    from backend.core.auth import _secret_value
    client_secret = _secret_value("AGENT_PLATFORM_OIDC_CLIENT_SECRET")
    if not issuer.startswith("https://") or not client_id or not client_secret:
        raise RuntimeError("OIDC requires HTTPS issuer, client id and client secret")

    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)
    client = oauth.register(
        name="enterprise_oidc",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=f"{issuer}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email"},
    )

    @app.route("/api/auth/oidc/start")
    def oidc_start():
        callback = f"{_public_url()}/api/auth/oidc/callback"
        return client.authorize_redirect(callback)

    @app.route("/api/auth/oidc/callback")
    def oidc_callback():
        try:
            token = client.authorize_access_token()
            userinfo = token.get("userinfo")
            if not userinfo:
                userinfo = client.userinfo(token=token)
            claims = dict(userinfo)
            username = _username_from_claims(claims)
            from backend.core.identity import get_user
            identity_user = get_user(username)
            if not identity_user or not identity_user.get("enabled", True):
                return jsonify({"ok": False, "error": "oidc_user_not_provisioned"}), 403
            from backend.core.auth import establish_identity_session
            establish_identity_session(identity_user)
            session["agent_platform_auth_method"] = "oidc"
            return redirect(f"{_public_url()}/workbench")
        except Exception:
            app.logger.warning("OIDC callback failed", exc_info=True)
            return jsonify({"ok": False, "error": "oidc_login_failed"}), 401
