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
    with app.test_request_context(
        "/api/agent/sse",
        headers={"X-API-Key": "secret-token"},
    ):
        assert _extract_token_from_request() == "secret-token"
