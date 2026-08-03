# backend/core/auth.py
"""Global API authentication middleware.

Environment variables:
  AGENT_PLATFORM_AUTH_ENABLED  — "true" or "false" (default: false)
  AGENT_PLATFORM_API_TOKEN     — shared secret for Bearer / X-API-Key auth
  AGENT_PLATFORM_LOGIN_ENABLED — "true" or "false" (default: true when username/password are set)
  AGENT_PLATFORM_LOGIN_USERNAME — web login username
  AGENT_PLATFORM_LOGIN_PASSWORD — web login password

Public endpoints (no auth required even when enabled):
  - /api/health, /health
  - /api/auth/login, /api/auth/status
  - Static frontend resources (non-/api/* paths)

Auth methods:
  - Authorization: Bearer <token>
  - X-API-Key: <token>

Returns 401 on auth failure:
  {"ok": false, "error": "unauthorized", "message": "...", "status": 401}
"""

import os
import logging
import hmac
import ipaddress
import secrets
from functools import wraps
from urllib.parse import urlparse

import flask

logger = logging.getLogger("agent_platform_base.auth")

def _is_auth_enabled() -> bool:
    """Read AGENT_PLATFORM_AUTH_ENABLED from env (re-evaluated each call for testability)."""
    return os.environ.get("AGENT_PLATFORM_AUTH_ENABLED", "false").strip().lower() in (
        "true", "1", "yes", "on",
    )


def _get_api_token() -> str:
    """Read AGENT_PLATFORM_API_TOKEN from env (re-evaluated each call for testability)."""
    return os.environ.get("AGENT_PLATFORM_API_TOKEN", "").strip()


def _get_login_username() -> str:
    return os.environ.get("AGENT_PLATFORM_LOGIN_USERNAME", "").strip()


def _get_login_password() -> str:
    return os.environ.get("AGENT_PLATFORM_LOGIN_PASSWORD", "")


def _is_login_enabled() -> bool:
    raw = os.environ.get("AGENT_PLATFORM_LOGIN_ENABLED", "").strip().lower()
    if raw:
        return raw in ("true", "1", "yes", "on")
    return bool(_get_login_username() and _get_login_password())


def _is_identity_enabled() -> bool:
    try:
        from backend.core.identity import identity_enabled
        return identity_enabled()
    except Exception:
        return False


# ── Module-level defaults (used for logging) ──
_AUTH_ENABLED = _is_auth_enabled()
_API_TOKEN = _get_api_token()

# ── Public endpoints (no auth required) ──
_PUBLIC_PREFIXES = frozenset([
    "/api/health",
    "/api/ready",
    "/api/auth/login",
    "/api/auth/status",
    "/health",
])

_PUBLIC_EXACT = frozenset([
    "/",
])


def is_public_path(path: str) -> bool:
    """Check if a request path is public (no auth required)."""
    if path == "/metrics":
        return False
    # Exact matches
    if path in _PUBLIC_EXACT:
        return True
    # Prefix matches
    for prefix in _PUBLIC_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    # Non-API paths (static frontend resources)
    if not path.startswith("/api/"):
        return True
    return False


def _unauthorized_response(message: str = "Missing or invalid API token") -> flask.Response:
    """Return a standardized 401 response."""
    return flask.jsonify({
        "ok": False,
        "error": "unauthorized",
        "message": message,
        "status": 401,
    }), 401


def _login_disabled_response() -> flask.Response:
    return flask.jsonify({
        "ok": False,
        "error": "login_disabled",
        "message": "Login is not enabled on this server.",
        "status": 404,
    }), 404


def _extract_token_from_request() -> str | None:
    """Extract bearer or API-key token from request headers.

    Does NOT log the token value.
    """
    # Authorization: Bearer <token>
    auth_header = flask.request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    # X-API-Key: <token>
    api_key = flask.request.headers.get("X-API-Key", "").strip()
    if api_key:
        return api_key

    # EventSource cannot set custom headers, so SSE clients may pass a token
    # in the query string. The value is never logged.
    query_token = flask.request.args.get("access_token", "").strip()
    if query_token:
        return query_token

    return None


def _request_has_valid_api_token() -> bool:
    api_token = _get_api_token()
    token = _extract_token_from_request()
    return bool(api_token and token and hmac.compare_digest(str(token), str(api_token)))


def is_current_session_authenticated() -> bool:
    if _is_identity_enabled():
        username = str(flask.session.get("agent_platform_user") or "")
        if not username:
            return False
        from backend.core.identity import get_user
        identity_user = get_user(username)
        if identity_user and identity_user.get("enabled", True):
            flask.session["agent_platform_role"] = identity_user.get("role", "viewer")
            flask.session["agent_platform_org"] = identity_user.get("organization_id", "default")
            flask.session["agent_platform_workspaces"] = list(identity_user.get("workspace_ids") or [])
            flask.session["agent_platform_home_workspace"] = identity_user.get("home_workspace_id", "")
            return True
        configured_username = _get_login_username()
        if configured_username and hmac.compare_digest(username, configured_username):
            flask.session["agent_platform_role"] = "admin"
            flask.session["agent_platform_org"] = "default"
            flask.session["agent_platform_workspaces"] = ["default"]
            flask.session["agent_platform_home_workspace"] = "default"
            return True
        flask.session.clear()
        return False
    if not _is_login_enabled():
        return False
    username = _get_login_username()
    session_user = flask.session.get("agent_platform_user")
    return bool(username and session_user and hmac.compare_digest(str(session_user), username))


def handle_auth_status():
    authenticated = is_current_session_authenticated()
    platform_admin = False
    if authenticated and _is_identity_enabled():
        try:
            from backend.core.identity import get_user
            current = get_user(str(flask.session.get("agent_platform_user") or ""))
            platform_admin = current is None or str(flask.session.get("agent_platform_role") or "") == "owner"
        except Exception:
            platform_admin = False
    return flask.jsonify({
        "ok": True,
        "login_enabled": _is_login_enabled() or _is_identity_enabled(),
        "authenticated": authenticated,
        "username": flask.session.get("agent_platform_user") if authenticated else "",
        "role": flask.session.get("agent_platform_role", "") if authenticated else "",
        "organization_id": flask.session.get("agent_platform_org", "") if authenticated else "",
        "workspace_ids": list(flask.session.get("agent_platform_workspaces") or []) if authenticated else [],
        "home_workspace_id": flask.session.get("agent_platform_home_workspace", "") if authenticated else "",
        "identity_enabled": _is_identity_enabled(),
        "platform_admin": platform_admin,
    })


def handle_auth_login():
    if not (_is_login_enabled() or _is_identity_enabled()):
        return _login_disabled_response()
    payload = flask.request.get_json(silent=True) or {}
    username = str(payload.get("username", ""))
    password = str(payload.get("password", ""))
    configured_username = _get_login_username()
    configured_password = _get_login_password()
    identity_user = None
    if _is_identity_enabled():
        from backend.core.identity import verify_user
        identity_user = verify_user(username, password)
        if identity_user:
            flask.session.clear()
            flask.session["agent_platform_user"] = identity_user["username"]
            flask.session["agent_platform_role"] = identity_user.get("role", "viewer")
            flask.session["agent_platform_org"] = identity_user.get("organization_id", "default")
            flask.session["agent_platform_workspaces"] = list(identity_user.get("workspace_ids") or [identity_user.get("organization_id", "default")])
            flask.session["agent_platform_home_workspace"] = identity_user.get("home_workspace_id", "")
            return flask.jsonify({"ok": True, "username": identity_user["username"], "role": identity_user.get("role", "viewer")})
    if (
        configured_username
        and configured_password
        # compare_digest(str, str) raises TypeError for non-ASCII input.
        # UTF-8 bytes preserve constant-time comparison and make invalid
        # credentials return the normal 401 response instead of HTTP 500.
        and hmac.compare_digest(username.encode("utf-8"), configured_username.encode("utf-8"))
        and hmac.compare_digest(password.encode("utf-8"), configured_password.encode("utf-8"))
    ):
        flask.session.clear()
        flask.session["agent_platform_user"] = configured_username
        if _is_identity_enabled():
            flask.session["agent_platform_role"] = "admin"
            flask.session["agent_platform_org"] = "default"
            flask.session["agent_platform_workspaces"] = ["default"]
            flask.session["agent_platform_home_workspace"] = "default"
        return flask.jsonify({"ok": True, "username": configured_username})
    logger.warning("login_denied: username=%s", username[:64])
    return _unauthorized_response("Invalid username or password")


def handle_auth_logout():
    flask.session.clear()
    return flask.jsonify({"ok": True})


def _configured_dev_origins() -> set[str]:
    raw = os.environ.get("AGENT_PLATFORM_ALLOWED_ORIGINS", "")
    origins = {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}
    ports = _configured_workbench_ports()
    for port in ports:
        origins.update({
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
            f"http://[::1]:{port}",
        })
    return origins


def _configured_workbench_ports() -> set[int]:
    raw = os.environ.get("AGENT_PLATFORM_WORKBENCH_PORTS", "5273,5274")
    ports: set[int] = set()
    for item in raw.split(","):
        try:
            port = int(item.strip())
        except ValueError:
            continue
        if 1 <= port <= 65535:
            ports.add(port)
    return ports or {5273, 5274}


def _is_local_or_private_host(hostname: str) -> bool:
    value = (hostname or "").strip().lower()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return value.endswith(".local")
    shared_cgnat = ipaddress.ip_network("100.64.0.0/10")
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local or ip in shared_cgnat)


def is_allowed_browser_origin(origin: str | None, request_host: str) -> bool:
    """Return True when a browser write comes from this API host or the local workbench."""
    if not origin:
        return True
    try:
        origin_url = urlparse(origin)
        origin_root = f"{origin_url.scheme}://{origin_url.netloc}".rstrip("/")
        host = request_host.split("@")[-1]
        # Same host (any port) = same machine = allow
        origin_hostname = origin_url.hostname or ""
        request_hostname = host.split(":")[0]
        if origin_hostname == request_hostname:
            return True
        origin_port = origin_url.port or (443 if origin_url.scheme == "https" else 80)
        if (
            origin_url.scheme in {"http", "https"}
            and origin_port in _configured_workbench_ports()
            and _is_local_or_private_host(origin_hostname)
            and _is_local_or_private_host(request_hostname)
        ):
            return True
        return origin_root in _configured_dev_origins()
    except Exception:
        return False


def _same_origin_api_request() -> bool:
    """Reject browser cross-site writes when token auth is disabled."""
    if flask.request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    origin = flask.request.headers.get("Origin") or flask.request.headers.get("Referer")
    return is_allowed_browser_origin(origin, flask.request.host)


def _csrf_response() -> flask.Response:
    return flask.jsonify({
        "ok": False,
        "error": "csrf_origin_denied",
        "message": "Cross-origin API writes are denied.",
        "status": 403,
    }), 403


def register_auth_middleware(app: flask.Flask) -> None:
    """Register before_request auth middleware on a Flask app.

    Call after all routes are defined but before first request.
    """
    if _is_login_enabled() or _is_identity_enabled():
        app.secret_key = os.environ.get("AGENT_PLATFORM_SESSION_SECRET", "").strip() or _get_api_token() or secrets.token_urlsafe(32)
        app.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE=os.environ.get("AGENT_PLATFORM_SESSION_SAMESITE", "Lax"),
            SESSION_COOKIE_SECURE=os.environ.get("AGENT_PLATFORM_SESSION_SECURE", "false").strip().lower() in ("true", "1", "yes", "on"),
        )
        logger.info("Web login authentication enabled")

    if not _AUTH_ENABLED:
        logger.info("API token authentication disabled; CSRF origin checks remain enabled")
    elif not _API_TOKEN:
        logger.warning(
            "AGENT_PLATFORM_AUTH_ENABLED=true but AGENT_PLATFORM_API_TOKEN is empty! "
            "All protected endpoints will reject requests."
        )
    else:
        logger.info(
            "API authentication enabled — %d public prefixes, %d public exact paths",
            len(_PUBLIC_PREFIXES), len(_PUBLIC_EXACT),
        )

    @app.before_request
    def _auth_before_request():
        # OPTIONS preflight — always allow
        if flask.request.method == "OPTIONS":
            return None

        path = flask.request.path

        if path.startswith("/api/") and not _same_origin_api_request():
            logger.warning("csrf_denied: path=%s origin=%s", path, flask.request.headers.get("Origin", ""))
            return _csrf_response()

        # Re-evaluate env vars each request (for test monkeypatching)
        if _is_login_enabled() or _is_identity_enabled():
            if is_public_path(path):
                return None
            session_authenticated = is_current_session_authenticated()
            if session_authenticated or _request_has_valid_api_token():
                if session_authenticated:
                    from storage.principal import set_storage_principal
                    flask.g._storage_principal_token = set_storage_principal(
                        str(flask.session.get("agent_platform_user") or "")
                    )
                denied = _authorize_identity_request()
                return denied
            logger.warning("auth_denied: path=%s reason=no_login_session", path)
            return _unauthorized_response("Login required")

        if not _is_auth_enabled():
            return None

        # Public endpoints — no auth
        if is_public_path(path):
            return None

        # Protected endpoints — require token
        token = _extract_token_from_request()
        api_token = _get_api_token()

        if not api_token:
            logger.error("auth_denied: AGENT_PLATFORM_API_TOKEN is empty but auth is enabled")
            return _unauthorized_response("Server authentication misconfigured — no API token set")

        if not token:
            logger.warning("auth_denied: path=%s reason=no_token", path)
            return _unauthorized_response("Missing API token — provide Authorization: Bearer <token> or X-API-Key: <token>")

        # Constant-time comparison: prevents timing-based token leakage.
        if not hmac.compare_digest(str(token), str(api_token)):
            logger.warning("auth_denied: path=%s reason=invalid_token", path)
            return _unauthorized_response("Invalid API token")

        # Token valid — proceed
        return None

    # Register teardown to clean up any auth state if needed
    @app.teardown_request
    def _auth_teardown(exc=None):
        token = getattr(flask.g, "_storage_principal_token", None)
        if token is not None:
            from storage.principal import reset_storage_principal
            reset_storage_principal(token)


def _authorize_identity_request():
    """Enforce workspace and control-plane RBAC after authentication."""
    if not _is_identity_enabled() or _request_has_valid_api_token():
        return None
    path = flask.request.path
    role = str(flask.session.get("agent_platform_role") or "viewer")
    from backend.core.identity import get_user
    current_user = get_user(str(flask.session.get("agent_platform_user") or ""))
    platform_admin = current_user is None or role == "owner"
    if path.startswith("/api/identity/") and not _role_at_least(role, "admin"):
        return flask.jsonify({"ok": False, "error": "forbidden"}), 403
    if path == "/api/workspaces" and flask.request.method == "POST" and not _role_at_least(role, "admin"):
        return flask.jsonify({"ok": False, "error": "forbidden"}), 403
    if path == "/api/workspaces/batch-delete" and not _role_at_least(role, "admin"):
        return flask.jsonify({"ok": False, "error": "forbidden"}), 403
    if path.startswith("/api/admin/") and not _role_at_least(role, "admin"):
        return flask.jsonify({"ok": False, "error": "admin_required"}), 403
    if path == "/api/workflows" and flask.request.method == "POST" and not _role_at_least(role, "developer"):
        return flask.jsonify({"ok": False, "error": "workflow_developer_required"}), 403
    if path.startswith("/api/workflows/") and flask.request.method in {"PUT", "DELETE"} and not _role_at_least(role, "developer"):
        return flask.jsonify({"ok": False, "error": "workflow_developer_required"}), 403
    if (path.startswith("/api/workflows/") and path.endswith("/runs") or path.startswith("/api/workflow-runs/")) and flask.request.method == "POST" and not _role_at_least(role, "operator"):
        return flask.jsonify({"ok": False, "error": "workflow_operator_required"}), 403
    if path.startswith("/api/agent/llm/") and flask.request.method not in {"GET", "HEAD"} and not _role_at_least(role, "admin"):
        return flask.jsonify({"ok": False, "error": "forbidden"}), 403
    if path.startswith("/api/extensions/"):
        extension_denied = _authorize_extension_request(path, role)
        if extension_denied:
            return extension_denied
    workspace_id = _request_workspace_id()
    if not workspace_id:
        return None
    if platform_admin:
        return None
    try:
        from backend.core.identity import can_access_workspace
        allowed = can_access_workspace(role, list(flask.session.get("agent_platform_workspaces") or []), workspace_id, write=flask.request.method not in {"GET", "HEAD"})
    except Exception:
        allowed = False
    if not allowed:
        return flask.jsonify({"ok": False, "error": "workspace_forbidden"}), 403
    return None


def _request_workspace_id() -> str:
    import re
    match = re.match(r"^/api/workspaces/([^/]+)", flask.request.path)
    if match and match.group(1) not in {"batch-delete"}:
        return match.group(1)
    value = flask.request.args.get("workspace_id", "")
    if value:
        return str(value)
    if flask.request.is_json:
        data = flask.request.get_json(silent=True) or {}
        return str(data.get("workspace_id") or "")
    return ""


def _role_at_least(role: str, minimum: str) -> bool:
    from backend.core.identity import has_role
    return has_role(role, minimum)


def _authorize_extension_request(path: str, role: str):
    import re
    if path.startswith("/api/extensions/repository") and flask.request.method not in {"GET", "HEAD"} and not _role_at_least(role, "admin"):
        return flask.jsonify({"ok": False, "error": "extension_admin_required"}), 403
    lifecycle = re.match(r"^/api/extensions/([^/]+)/(enable|disable|migrate|install|upgrade|uninstall)", path)
    if lifecycle and not _role_at_least(role, "admin"):
        return flask.jsonify({"ok": False, "error": "extension_admin_required"}), 403
    match = re.match(r"^/api/extensions/([^/]+)", path)
    if not match:
        return None
    extension_id = match.group(1)
    try:
        from extensions.registry import ExtensionRegistry
        manifest = next((item for item in ExtensionRegistry().discover() if item.extension_id == extension_id), None)
    except Exception:
        manifest = None
    if manifest is None:
        return None
    if not lifecycle:
        from extensions.state import get_extension_state
        if not get_extension_state(extension_id, default_enabled=manifest.enabled)["enabled"]:
            return flask.jsonify({"ok": False, "error": "extension_disabled"}), 409
    minimum = str(manifest.metadata.get("minimum_role") or "viewer")
    if not _role_at_least(role, minimum):
        return flask.jsonify({"ok": False, "error": "extension_role_forbidden"}), 403
    if flask.request.method not in {"GET", "HEAD"}:
        write_role = str(manifest.metadata.get("minimum_write_role") or "developer")
        if not _role_at_least(role, write_role):
            return flask.jsonify({"ok": False, "error": "extension_write_forbidden"}), 403
    return None
