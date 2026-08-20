from agent.runtime.session_events import push_event, subscribe
from storage.principal import storage_principal
import pytest


def test_sse_events_are_isolated_by_storage_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    with storage_principal("alice"):
        push_event("session_shared", "token", {"text": "alice-only"}, workspace_id="team")

    with storage_principal("bob"):
        assert subscribe("session_shared", timeout=0, workspace_id="team") is None

    with storage_principal("alice"):
        frame = subscribe("session_shared", timeout=0, workspace_id="team")
        assert frame is not None
        assert "alice-only" in frame


def test_session_event_queue_is_bounded_and_keeps_newest(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    with storage_principal("alice"):
        for index in range(300):
            push_event("session_bounded", "token", {"index": index}, workspace_id="team")
        frames = [subscribe("session_bounded", timeout=0, workspace_id="team") for _ in range(256)]
    assert all(frame is not None for frame in frames)
    assert '"index": 44' in frames[0]
    assert '"index": 299' in frames[-1]


def test_session_events_require_workspace_scope():
    with pytest.raises(TypeError):
        push_event("session_unscoped", "token", {})
