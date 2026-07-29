# harness/test_rate_limit_per_ip.py
"""Rate Limit per-IP + endpoint tests.

Tests:
1. Same IP, same endpoint: 429 when exceed limit
2. Different IPs, same endpoint: don't affect each other
3. TRUSTED_PROXY=false: don't trust X-Forwarded-For
4. TRUSTED_PROXY=true: trust X-Forwarded-For
"""

import os
import time
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate_rate_limit(monkeypatch):
    """Isolate rate limiter state and env between tests."""
    monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY", raising=False)
    from backend.core.rate_limit import clear_rate_limit_state_for_tests
    clear_rate_limit_state_for_tests()
    yield
    clear_rate_limit_state_for_tests()


@pytest.fixture
def app_with_rate_limit():
    """Create a Flask app with rate limiting enabled."""
    from flask import Flask
    from backend.core.rate_limit import rate_limit_middleware

    app = Flask(__name__)
    app.config["TESTING"] = False  # Enable rate limiting
    rate_limit_middleware(app)

    @app.route("/api/agent/message", methods=["POST"])
    def api_agent_run():
        return {"ok": True}

    @app.route("/api/tools/invoke", methods=["POST"])
    def api_tools_invoke():
        return {"ok": True}

    @app.route("/api/public", methods=["GET"])
    def api_public():
        return {"ok": True}

    return app


@pytest.fixture
def client(app_with_rate_limit):
    """Create a test client."""
    app_with_rate_limit.config["TESTING"] = False
    return app_with_rate_limit.test_client()


class TestSameIPSameEndpointLimit:
    """Same IP, same endpoint should get 429 when exceed limit."""
    
    def setup_method(self, method):
        """Ensure TESTING=False before each test."""
        from backend.core.rate_limit import _limiters
        _limiters.clear()
    
    def test_exceed_agent_run_limit(self, client, app_with_rate_limit):
        """/api/agent/message allows 10 req/min, 11th should 429."""
        app_with_rate_limit.config["TESTING"] = False

        # Send 10 requests first (should succeed)
        for i in range(10):
            resp = client.post(
                "/api/agent/message",
                json={"message": f"test {i}"},
                environ_base={"REMOTE_ADDR": "192.168.1.100"},
            )
            assert resp.status_code == 200, f"Request {i} should succeed"

        # 11th request should be rate limited
        resp = client.post(
            "/api/agent/message",
            json={"message": "test 11"},
            environ_base={"REMOTE_ADDR": "192.168.1.100"},
        )
        assert resp.status_code == 429
        data = resp.get_json()
        assert data["error"] == "rate_limit_exceeded"
        assert "retry_after_seconds" in data

    def test_exceed_tools_invoke_limit(self, client):
        """"/api/tools/invoke allows 30 req/min, 31st should 429."""
        from backend.core.rate_limit import _limiters
        _limiters.clear()

        for i in range(30):
            resp = client.post(
                "/api/tools/invoke",
                json={"tool_id": "test"},
                environ_base={"REMOTE_ADDR": "192.168.1.101"},
            )
            assert resp.status_code == 200, f"Request {i} should succeed"

        resp = client.post(
            "/api/tools/invoke",
            json={"tool_id": "test"},
            environ_base={"REMOTE_ADDR": "192.168.1.101"},
        )
        assert resp.status_code == 429


class TestDifferentIPsIndependent:
    """Different IPs should have independent rate limit buckets."""

    def test_different_ips_same_endpoint(self, client):
        """IP A exceeding limit should not affect IP B."""
        from backend.core.rate_limit import _limiters
        _limiters.clear()

        # IP A: send 10 requests (should succeed)
        for i in range(10):
            resp = client.post(
                "/api/agent/message",
                json={"message": f"test {i}"},
                environ_base={"REMOTE_ADDR": "192.168.1.100"},
            )
            assert resp.status_code == 200

        # IP A: 11th request should 429
        resp = client.post(
            "/api/agent/message",
            json={"message": "test 11"},
            environ_base={"REMOTE_ADDR": "192.168.1.100"},
        )
        assert resp.status_code == 429

        # IP B: should still succeed (independent bucket)
        resp = client.post(
            "/api/agent/message",
            json={"message": "test B"},
            environ_base={"REMOTE_ADDR": "192.168.1.200"},
        )
        assert resp.status_code == 200

    def test_different_ips_different_buckets(self, client):
        """Verify different IPs get different rate limit buckets."""
        from backend.core.rate_limit import _limiters
        _limiters.clear()

        # Make requests from 3 different IPs
        for ip in ["10.0.0.1", "10.0.0.2", "10.0.0.3"]:
            for i in range(5):
                client.post(
                    "/api/agent/message",
                    json={"message": f"test {i}"},
                    environ_base={"REMOTE_ADDR": ip},
                )

        # Check that we have 3 different limiters (one per IP)
        keys = list(_limiters.keys())
        ip_prefixes = set(k.split(":")[0] for k in keys)
        assert len(ip_prefixes) == 3, f"Expected 3 different IPs in limiter keys, got {ip_prefixes}"


class TestTrustedProxyFalse:
    """TRUSTED_PROXY=false: should not trust X-Forwarded-For."""

    def test_x_forwarded_for_ignored_when_false(self, client):
        """When TRUSTED_PROXY=false, X-Forwarded-For should be ignored."""
        from backend.core.rate_limit import _limiters
        _limiters.clear()

        # Clear env
        os.environ.pop("TRUSTED_PROXY", None)

        # Request with X-Forwarded-For but different REMOTE_ADDR
        # Should use REMOTE_ADDR (127.0.0.1), not X-Forwarded-For
        for i in range(10):
            resp = client.post(
                "/api/agent/message",
                json={"message": f"test {i}"},
                headers={"X-Forwarded-For": "8.8.8.8"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
            assert resp.status_code == 200

        # 11th request from 127.0.0.1 should 429
        resp = client.post(
            "/api/agent/message",
            json={"message": "test 11"},
            headers={"X-Forwarded-For": "8.8.8.8"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert resp.status_code == 429

        # But a request from 8.8.8.8 (spoofed) should succeed
        # because we don't trust X-Forwarded-For
        resp = client.post(
            "/api/agent/message",
            json={"message": "test spoofed"},
            environ_base={"REMOTE_ADDR": "8.8.8.8"},
        )
        assert resp.status_code == 200


class TestTrustedProxyTrue:
    """TRUSTED_PROXY=true: should trust X-Forwarded-For."""

    def test_x_forwarded_for_trusted_when_true(self, client, monkeypatch):
        """When TRUSTED_PROXY=true, X-Forwarded-For should be used."""
        from backend.core.rate_limit import _limiters
        _limiters.clear()

        # Set TRUSTED_PROXY=true
        monkeypatch.setenv("TRUSTED_PROXY", "true")

        # Request with X-Forwarded-For
        # Should use 8.8.8.8 (from X-Forwarded-For), not 127.0.0.1
        for i in range(10):
            resp = client.post(
                "/api/agent/message",
                json={"message": f"test {i}"},
                headers={"X-Forwarded-For": "8.8.8.8"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
            assert resp.status_code == 200

        # 11th request with same X-Forwarded-For should 429
        resp = client.post(
            "/api/agent/message",
            json={"message": "test 11"},
            headers={"X-Forwarded-For": "8.8.8.8"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert resp.status_code == 429

        # But a request from a different IP (even with different X-Forwarded-For) should succeed
        resp = client.post(
            "/api/agent/message",
            json={"message": "test other"},
            headers={"X-Forwarded-For": "1.1.1.1"},
            environ_base={"REMOTE_ADDR": "127.0.0.2"},
        )
        assert resp.status_code == 200
