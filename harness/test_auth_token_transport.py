from flask import Flask

from backend.core.auth import _extract_token_from_request


def test_api_token_is_never_accepted_from_query_string():
    app = Flask(__name__)
    with app.test_request_context("/api/agent/sse?access_token=secret-token"):
        assert _extract_token_from_request() is None


def test_api_token_headers_remain_supported():
    app = Flask(__name__)
    with app.test_request_context(
        "/api/agent/sse",
        headers={"Authorization": "Bearer secret-token"},
    ):
        assert _extract_token_from_request() == "secret-token"


def test_api_token_auth_status_is_a_browser_usable_owner_session(monkeypatch):
    monkeypatch.setenv("AGENT_PLATFORM_API_TOKEN", "secret-token")
    monkeypatch.setenv("AGENT_PLATFORM_AUTH_ENABLED", "true")
    from backend.core.auth import handle_auth_status

    app = Flask(__name__)
    app.secret_key = "test-secret"
    with app.test_request_context(
        "/api/auth/status",
        headers={"Authorization": "Bearer secret-token"},
    ):
        status = handle_auth_status().get_json()
    assert status["authenticated"] is True
    assert status["username"] == "api-token"
    assert status["role"] == "owner"
    assert status["platform_admin"] is True
    assert status["auth_type"] == "api_token"
    with app.test_request_context(
        "/api/agent/sse",
        headers={"X-API-Key": "secret-token"},
    ):
        assert _extract_token_from_request() == "secret-token"
