# backend/core/auth.py
"""Global API authentication middleware.

Environment variables:
  AGENT_PLATFORM_AUTH_ENABLED  — "true" or "false" (default: false)
  AGENT_PLATFORM_API_TOKEN     — shared secret for Bearer / X-API-Key auth

Public endpoints (no auth required even when enabled):
  - /api/health, /health
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


# ── Module-level defaults (used for logging) ──
_AUTH_ENABLED = _is_auth_enabled()
_API_TOKEN = _get_api_token()

# ── Public endpoints (no auth required) ──
_PUBLIC_PREFIXES = frozenset([
    "/api/health",
    "/health",
])

_PUBLIC_EXACT = frozenset([
    "/",
])


def is_public_path(path: str) -> bool:
    """Check if a request path is public (no auth required)."""
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


def _configured_dev_origins() -> set[str]:
    raw = os.environ.get("AGENT_PLATFORM_ALLOWED_ORIGINS", "")
    origins = {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}
    origins.update({
        "http://localhost:5273",
        "http://127.0.0.1:5273",
        "http://[::1]:5273",
    })
    return origins


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
        pass  # No persistent auth state to clean up
