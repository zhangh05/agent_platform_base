# harness/test_approval_guard.py
"""Unified approval API tests — single ApprovalStore, no legacy fallback.

Tests:
1. Non-localhost + no X-Admin-Token: 403
2. Correct token: resolve succeeds
3. Wrong token: 403
4. Pending -> approved -> can verify via history
   Pending -> rejected -> can verify via history
5. Cross-workspace approval boundary
"""

import os
import pytest


@pytest.fixture
def app_with_approvals():
    """Create a Flask app with unified approval routes."""
    from flask import Flask
    from backend.api.approval_routes import register_approval_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    register_approval_routes(app)
    return app


@pytest.fixture
def client(app_with_approvals):
    """Create a test client."""
    return app_with_approvals.test_client()


@pytest.fixture
def reset_approvals(tmp_path, monkeypatch):
    """Reset the unified ApprovalStore before each test."""
    import agent.approval as approval_module
    from agent.approval import reset_approval_store_for_tests

    monkeypatch.setattr(approval_module, "_APPROVALS_FILE", tmp_path / "tool_approvals.jsonl")
    reset_approval_store_for_tests(remove_persisted=True)
    yield
    reset_approval_store_for_tests(remove_persisted=True)


class TestAdminTokenAuth:
    """Test X-Admin-Token authentication on resolve endpoint."""

    def test_correct_token_resolve_succeeds(self, client, reset_approvals, monkeypatch):
        """With correct X-Admin-Token, resolve should succeed."""
        monkeypatch.setenv("AGENT_PLATFORM_ADMIN_TOKEN", "secret-admin-token")

        # Create approval via store directly
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-1", tool_id="test.tool",
            arguments={"cmd": "ls"}, description="test",
            risk_level="high", workspace_id="ws_a",
        )

        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={"decision": "approve", "workspace_id": "ws_a", "resolver": "admin"},
            headers={"X-Admin-Token": "secret-admin-token"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["decision"] == "approve"

    def test_resolve_requires_workspace_id(self, client, reset_approvals, monkeypatch):
        """Resolve must be scoped to the approval workspace."""
        monkeypatch.setenv("AGENT_PLATFORM_ADMIN_TOKEN", "secret-admin-token")

        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-scope", tool_id="test.tool",
            arguments={"cmd": "ls"}, description="test",
            risk_level="high", workspace_id="ws_scope",
        )

        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={"decision": "approve"},
            headers={"X-Admin-Token": "secret-admin-token"},
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "workspace_id is required"

    def test_resolve_rejects_wrong_workspace(self, client, reset_approvals, monkeypatch):
        """Approval ids cannot be resolved from another workspace."""
        monkeypatch.setenv("AGENT_PLATFORM_ADMIN_TOKEN", "secret-admin-token")

        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-scope", tool_id="test.tool",
            arguments={"cmd": "ls"}, description="test",
            risk_level="high", workspace_id="ws_scope",
        )

        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={"decision": "approve", "workspace_id": "ws_other"},
            headers={"X-Admin-Token": "secret-admin-token"},
        )
        assert resp.status_code == 404
        assert store.get_pending(workspace_id="ws_scope")[0]["approval_id"] == req.approval_id

    def test_feedback_decision_resolves_pending_approval(self, client, reset_approvals, monkeypatch):
        monkeypatch.setenv("AGENT_PLATFORM_ADMIN_TOKEN", "secret-admin-token")
        from agent.approval import get_approval_store

        store = get_approval_store()
        req = store.create(
            session_id="sess-feedback", tool_id="exec.run",
            arguments={"cmd": "rm -f old.log"}, description="feedback",
            risk_level="high", workspace_id="ws_feedback",
        )
        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={
                "decision": "respond_with_feedback",
                "feedback": "改用非破坏性方案",
                "workspace_id": "ws_feedback",
            },
            headers={"X-Admin-Token": "secret-admin-token"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["feedback_recorded"] is True
        assert store.get_pending(workspace_id="ws_feedback") == []

    def test_wrong_token_returns_403(self, client, reset_approvals, monkeypatch):
        """Wrong token should return 403."""
        monkeypatch.setenv("AGENT_PLATFORM_ADMIN_TOKEN", "secret-admin-token")

        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-2", tool_id="test.tool",
            arguments={"cmd": "ls"}, description="test",
            risk_level="high", workspace_id="ws_a",
        )

        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={"decision": "approve", "workspace_id": "ws_a"},
            headers={"X-Admin-Token": "wrong-token"},
        )
        assert resp.status_code == 403

    def test_no_token_when_configured_returns_403(self, client, reset_approvals, monkeypatch):
        """No token when required should return 403."""
        monkeypatch.setenv("AGENT_PLATFORM_ADMIN_TOKEN", "required-token")

        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-3", tool_id="test.tool",
            arguments={"cmd": "ls"}, description="test",
            risk_level="high", workspace_id="ws_a",
        )

        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={"decision": "approve", "workspace_id": "ws_a"},
        )
        assert resp.status_code == 403


class TestApprovalLifecycle:
    """Test create -> resolve -> history lifecycle."""

    def test_pending_listing(self, client, reset_approvals):
        """Pending approvals should be listable via the unified API."""
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-life", tool_id="exec.run",
            arguments={"cmd": "ls"}, description="pending test",
            risk_level="high", workspace_id="ws_life",
        )

        resp = client.get("/api/agent/approvals/pending?workspace_id=ws_life&session_id=sess-life")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["count"] >= 1
        pending_ids = [p["approval_id"] for p in data["pending"]]
        assert req.approval_id in pending_ids

    def test_approve_and_history(self, client, reset_approvals):
        """Approved items appear in history."""
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-hist", tool_id="exec.run",
            arguments={"cmd": "ls"}, description="history test",
            risk_level="high", workspace_id="ws_hist",
        )

        # Resolve
        from agent.approval import get_approval_store
        store.resolve(req.approval_id, True, workspace_id="ws_hist", resolver="test")

        resp = client.get("/api/agent/approvals/history?workspace_id=ws_hist&session_id=sess-hist&limit=10")
        data = resp.get_json()
        assert data["ok"] is True
        history_ids = [h["approval_id"] for h in data["history"]]
        assert req.approval_id in history_ids

    def test_rejected_appears_in_history(self, client, reset_approvals):
        """Rejected items appear in history."""
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-rej", tool_id="exec.run",
            arguments={"cmd": "rm"}, description="rejected test",
            risk_level="high", workspace_id="ws_rej",
        )

        store.resolve(req.approval_id, False, workspace_id="ws_rej", resolver="test")

        resp = client.get("/api/agent/approvals/history?workspace_id=ws_rej")
        data = resp.get_json()
        history_ids = [h["approval_id"] for h in data["history"]]
        assert req.approval_id in history_ids

    def test_history_since_accepts_iso_created_at(self, client, reset_approvals):
        import time
        from agent.approval import get_approval_store

        store = get_approval_store()
        req = store.create(
            session_id="sess-since", tool_id="exec.run",
            arguments={"cmd": "rm -f old.log"}, description="since test",
            risk_level="high", workspace_id="ws_since",
        )
        store.resolve(req.approval_id, False, workspace_id="ws_since", resolver="test")

        included = client.get(
            f"/api/agent/approvals/history?workspace_id=ws_since&since={time.time() - 60}"
        )
        assert included.status_code == 200
        assert req.approval_id in [item["approval_id"] for item in included.get_json()["history"]]

        excluded = client.get(
            f"/api/agent/approvals/history?workspace_id=ws_since&since={time.time() + 60}"
        )
        assert excluded.status_code == 200
        assert req.approval_id not in [item["approval_id"] for item in excluded.get_json()["history"]]


class TestWorkspaceApprovalBoundary:
    """Test cross-workspace approval separation."""

    def test_approval_includes_workspace_id(self, reset_approvals):
        """Approval records carry workspace_id."""
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-ws", tool_id="exec.run",
            arguments={"cmd": "ls"}, description="ws test",
            risk_level="high", workspace_id="ws_a",
        )
        assert req.workspace_id == "ws_a"

    def test_history_filtered_by_session(self, client, reset_approvals):
        """History can be filtered by session_id."""
        from agent.approval import get_approval_store
        store = get_approval_store()
        req_a = store.create(
            session_id="sess-A", tool_id="exec.run",
            arguments={"cmd": "a"}, risk_level="high",
            workspace_id="ws_x",
        )
        req_b = store.create(
            session_id="sess-B", tool_id="exec.run",
            arguments={"cmd": "b"}, risk_level="high",
            workspace_id="ws_x",
        )

        store.resolve(req_a.approval_id, True, workspace_id="ws_x", resolver="test")
        store.resolve(req_b.approval_id, True, workspace_id="ws_x", resolver="test")

        # Filter by session A
        resp = client.get("/api/agent/approvals/history?workspace_id=ws_x&session_id=sess-A")
        data = resp.get_json()
        history_ids = [h["approval_id"] for h in data["history"]]
        assert req_a.approval_id in history_ids
        assert req_b.approval_id not in history_ids


class TestApprovalStoreContract:
    """Test core ApprovalStore guarantees."""

    def test_arguments_redacted_in_record(self, tmp_path):
        """Persisted records have redacted arguments."""
        from agent.approval import ApprovalStore
        store = ApprovalStore(persist_path=tmp_path / "test.jsonl")
        req = store.create(
            session_id="sess-redact", tool_id="exec.run",
            arguments={"password": "secret123", "user": "admin"},
            risk_level="high", workspace_id="ws_r",
        )
        store.resolve(req.approval_id, True, workspace_id="ws_r")

        history = store.get_history()
        assert len(history) >= 1
        rec = history[0]
        # password should be redacted
        args = rec.get("arguments", {})
        assert args.get("password") != "secret123"

    def test_create_returns_workspace_id(self, tmp_path):
        """ApprovalRequest carries workspace_id."""
        from agent.approval import ApprovalStore
        store = ApprovalStore(persist_path=tmp_path / "test.jsonl")
        req = store.create(
            session_id="sess-1", tool_id="exec.run",
            arguments={}, risk_level="high", workspace_id="my_ws",
        )
        assert req.workspace_id == "my_ws"

    def test_create_rejects_invalid_workspace_id(self, tmp_path):
        """ApprovalStore is not allowed to persist malformed workspace ids."""
        from agent.approval import ApprovalStore
        store = ApprovalStore(persist_path=tmp_path / "test.jsonl")

        with pytest.raises(ValueError):
            store.create(
                session_id="sess-1", tool_id="exec.run",
                arguments={}, risk_level="high", workspace_id="../../etc",
            )

    def test_create_returns_run_and_job_ids(self, tmp_path):
        """ApprovalRequest carries run_id and job_id."""
        from agent.approval import ApprovalStore
        store = ApprovalStore(persist_path=tmp_path / "test.jsonl")
        req = store.create(
            session_id="sess-1", tool_id="exec.run",
            arguments={}, risk_level="high",
            workspace_id="ws_x", run_id="run_99", job_id="job_42",
        )
        assert req.run_id == "run_99"
        assert req.job_id == "job_42"


def test_approval_store_cache_isolated_by_storage_principal(monkeypatch, tmp_path):
    import agent.approval as approval_module
    from agent.approval import get_approval_store, reset_approval_store_for_tests
    from storage.principal import storage_principal

    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(approval_module, "_APPROVALS_FILE", None)
    reset_approval_store_for_tests(remove_persisted=True)
    try:
        with storage_principal("alice"):
            alice = get_approval_store()
            alice.create("session_shared", "exec.run", {}, workspace_id="team")

        with storage_principal("bob"):
            bob = get_approval_store()
            assert bob is not alice
            assert bob._persist_path != alice._persist_path
            assert bob.get_pending(workspace_id="team") == []
    finally:
        reset_approval_store_for_tests(remove_persisted=True)
