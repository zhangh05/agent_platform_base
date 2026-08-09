from agent.runtime.session_events import push_event, subscribe
from storage.principal import storage_principal


def test_sse_events_are_isolated_by_storage_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    with storage_principal("alice"):
        push_event("session_shared", "token", {"text": "alice-only"}, workspace_id="team")

    with storage_principal("bob"):
        assert subscribe("session_shared", timeout=0, workspace_id="team") is None

    with storage_principal("alice"):
        frame = subscribe("session_shared", timeout=0, workspace_id="team")
        assert frame is not None
        assert "alice-only" in frame
