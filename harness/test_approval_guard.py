# harness/test_approval_guard.py
"""Unified approval API tests — single ApprovalStore, no legacy fallback.

Tests identity-bound resolution, workspace boundaries and lifecycle auditing.
"""

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


class TestApprovalIdentityAuthorization:
    """Resolution follows identity and role, never proxy/source address."""

    @staticmethod
    def _actor(monkeypatch, *, username="alice", actor_id="user-alice", role="viewer"):
        monkeypatch.setattr(
            "backend.core.auth.current_request_actor",
            lambda: {"username": username, "actor_id": actor_id, "role": role},
        )

    def test_requester_can_resolve_own_approval(self, client, reset_approvals, monkeypatch):
        self._actor(monkeypatch)
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-1", tool_id="test.tool",
            arguments={"cmd": "ls"}, description="test",
            risk_level="high", workspace_id="ws_a",
            requester="alice", requester_id="user-alice",
        )

        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={"decision": "approve", "workspace_id": "ws_a", "session_id": "sess-1", "resolver": "forged"},
            environ_base={"REMOTE_ADDR": "203.0.113.10"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["decision"] == "approve"
        history = store.get_history(workspace_id="ws_a")
        assert history[0]["resolver"] == "alice"

    def test_resolve_requires_workspace_id(self, client, reset_approvals, monkeypatch):
        self._actor(monkeypatch, role="admin")
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
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "workspace_id is required"

    def test_resolve_rejects_wrong_workspace(self, client, reset_approvals, monkeypatch):
        self._actor(monkeypatch, role="admin")
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-scope", tool_id="test.tool",
            arguments={"cmd": "ls"}, description="test",
            risk_level="high", workspace_id="ws_scope",
        )

        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={"decision": "approve", "workspace_id": "ws_other", "session_id": "sess-scope"},
        )
        assert resp.status_code == 404
        assert store.get_pending(workspace_id="ws_scope")[0]["approval_id"] == req.approval_id

    def test_feedback_decision_resolves_pending_approval(self, client, reset_approvals, monkeypatch):
        self._actor(monkeypatch, role="admin")
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
                "session_id": "sess-feedback",
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["feedback_recorded"] is True
        assert store.get_pending(workspace_id="ws_feedback") == []

    def test_interactive_approval_cannot_claim_edited_arguments(self, client, reset_approvals, monkeypatch):
        self._actor(monkeypatch, role="admin")
        from agent.approval import get_approval_store

        store = get_approval_store()
        req = store.create(
            session_id="sess-edit", tool_id="exec.run",
            arguments={"cmd": "rm -f old.log"}, description="edit",
            risk_level="high", workspace_id="ws_edit",
        )
        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={
                "decision": "edit_args",
                "edited_args": {"cmd": "ls"},
                "workspace_id": "ws_edit",
                "session_id": "sess-edit",
            },
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "approval_edit_args_not_supported"
        assert store.get_pending(workspace_id="ws_edit")[0]["approval_id"] == req.approval_id

    def test_other_non_admin_user_is_forbidden(self, client, reset_approvals, monkeypatch):
        self._actor(monkeypatch, username="bob", actor_id="user-bob", role="viewer")
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-2", tool_id="test.tool",
            arguments={"cmd": "ls"}, description="test",
            risk_level="high", workspace_id="ws_a",
            requester="alice", requester_id="user-alice",
        )

        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={"decision": "approve", "workspace_id": "ws_a", "session_id": "sess-2"},
        )
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "approval_resolver_forbidden"

    def test_admin_can_resolve_workspace_approval(self, client, reset_approvals, monkeypatch):
        self._actor(monkeypatch, username="Admin", actor_id="admin", role="admin")
        from agent.approval import get_approval_store
        store = get_approval_store()
        req = store.create(
            session_id="sess-3", tool_id="test.tool",
            arguments={"cmd": "ls"}, description="test",
            risk_level="high", workspace_id="ws_a",
            requester="alice", requester_id="user-alice",
        )

        resp = client.post(
            f"/api/agent/approvals/{req.approval_id}/resolve",
            json={"decision": "approve", "workspace_id": "ws_a", "session_id": "sess-3"},
        )
        assert resp.status_code == 200


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

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
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


def test_approval_sse_emits_snapshot_sync_after_subscription(client, reset_approvals):
    import json

    response = client.get(
        "/api/agent/approvals/sse?workspace_id=ws_sse_ready",
        buffered=False,
    )
    iterator = iter(response.response)
    assert next(iterator) == b": connected\n\n"
    event = next(iterator).decode("utf-8")
    assert event.startswith("data: ")
    payload = json.loads(event.removeprefix("data: ").strip())
    assert payload["kind"] == "stream_ready"
    assert payload["workspace_id"] == "ws_sse_ready"
    assert payload["payload"] == {"snapshot_required": True}
    response.close()
def test_canonical_executor_rejects_resolved_approval_bound_to_different_arguments(reset_approvals):
    from agent.approval import get_approval_store
    from core.tools.executor import ToolExecutor
    from core.tools.registry import ToolRegistry
    from core.tools.schemas import ToolInvocation, ToolSpec

    registry = ToolRegistry()
    observed = []
    registry.register_tool(
        ToolSpec(
            tool_id="test.approval_bound",
            name="approval-bound test",
            description="test-only high-risk action",
            category="tool",
            risk_level="high",
            requires_approval=True,
            input_schema={"type": "object", "required": ["target"]},
            permission_action="write",
        ),
        lambda invocation: observed.append(invocation.arguments) or {"ok": True},
    )
    store = get_approval_store("approval-ws")
    request = store.create(
        session_id="approval-session",
        tool_id="test.approval_bound",
        arguments={"target": "approved.txt"},
        description="delete approved.txt",
        risk_level="high",
        workspace_id="approval-ws",
        run_id="approval-run",
    )
    assert store.resolve(
        request.approval_id, allowed=True, workspace_id="approval-ws",
    ) is not None
    executor = ToolExecutor(registry)

    exact = executor.execute(ToolInvocation(
        tool_id="test.approval_bound",
        arguments={"target": "approved.txt"},
        workspace_id="approval-ws",
        run_id="approval-run",
        requested_by="turn_runner",
        approval_id=request.approval_id,
    ))
    mismatched = executor.execute(ToolInvocation(
        tool_id="test.approval_bound",
        arguments={"target": "other.txt"},
        workspace_id="approval-ws",
        run_id="approval-run",
        requested_by="turn_runner",
        approval_id=request.approval_id,
    ))

    resumed = executor.execute(ToolInvocation(
        tool_id="test.approval_bound",
        arguments={"target": "approved.txt"},
        workspace_id="approval-ws",
        run_id="continuation-run",
        approval_run_id="approval-run",
        requested_by="turn_runner",
        approval_id=request.approval_id,
    ))

    assert exact.status == "succeeded"
    assert resumed.status == "succeeded"
    assert mismatched.status == "blocked"
    assert mismatched.output["error"] == "invalid_approval_binding"
    assert observed == [{"target": "approved.txt"}, {"target": "approved.txt"}]

def test_resolve_rejects_same_workspace_different_session(client, reset_approvals, monkeypatch):
    TestApprovalIdentityAuthorization._actor(monkeypatch, role="admin")
    from agent.approval import get_approval_store

    store = get_approval_store()
    req = store.create(
        session_id="session-a",
        tool_id="workspace.file",
        arguments={"action": "delete", "filepath": "a.txt"},
        description="delete a.txt",
        risk_level="high",
        workspace_id="shared-workspace",
    )

    wrong = client.post(
        f"/api/agent/approvals/{req.approval_id}/resolve",
        json={
            "decision": "approve",
            "workspace_id": "shared-workspace",
            "session_id": "session-b",
        },
    )
    assert wrong.status_code == 409
    assert wrong.get_json()["error"] == "approval_session_mismatch"
    assert store.get_pending(session_id="session-a", workspace_id="shared-workspace")[0]["approval_id"] == req.approval_id

    exact = client.post(
        f"/api/agent/approvals/{req.approval_id}/resolve",
        json={
            "decision": "approve",
            "workspace_id": "shared-workspace",
            "session_id": "session-a",
        },
    )
    assert exact.status_code == 200


def test_approval_sse_does_not_cross_storage_principals_with_shared_workspace(client, reset_approvals, monkeypatch, tmp_path):
    import json
    import agent.approval as approval_module
    from agent.approval import get_approval_store
    from storage.principal import storage_principal

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(approval_module, "_APPROVALS_FILE", None)

    with storage_principal("bob"):
        response = client.get(
            "/api/agent/approvals/sse?workspace_id=default",
            buffered=False,
        )
    iterator = iter(response.response)
    assert next(iterator) == b": connected\n\n"
    assert json.loads(next(iterator).decode("utf-8").removeprefix("data: ").strip())["kind"] == "stream_ready"

    with storage_principal("alice"):
        alice = get_approval_store("default")
        alice_request = alice.create(
            "alice-session", "exec.run", {"command": "echo alice"},
            workspace_id="default", risk_level="high",
        )

    with storage_principal("bob"):
        bob = get_approval_store("default")
        bob_request = bob.create(
            "bob-session", "exec.run", {"command": "echo bob"},
            workspace_id="default", risk_level="high",
        )

    event = json.loads(next(iterator).decode("utf-8").removeprefix("data: ").strip())
    assert event["approval_id"] == bob_request.approval_id
    assert event["session_id"] == "bob-session"
    assert event["approval_id"] != alice_request.approval_id

    with storage_principal("bob"):
        assert bob.resolve(bob_request.approval_id, True, workspace_id="default") is not None
    resolved_event = json.loads(next(iterator).decode("utf-8").removeprefix("data: ").strip())
    response.close()
    assert resolved_event["kind"] == "resolved"
    assert resolved_event["approval_id"] == bob_request.approval_id
    assert resolved_event["session_id"] == "bob-session"
