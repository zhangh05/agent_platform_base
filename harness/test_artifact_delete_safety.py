"""Safety contract for the LLM-visible artifact deletion action."""

from core.tools.schemas import ToolInvocation


def test_workspace_artifact_delete_is_recoverable(monkeypatch):
    import artifacts.store as artifact_store
    from core.tools.general_tools.artifact_tools import handle_artifact_delete_soft

    called = {}

    def fake_delete(workspace_id, artifact_id, *, hard=False):
        called.update(workspace_id=workspace_id, artifact_id=artifact_id, hard=hard)
        return True

    monkeypatch.setattr(artifact_store, "delete_artifact", fake_delete)
    result = handle_artifact_delete_soft(ToolInvocation(
        tool_id="workspace.artifact", workspace_id="test_ws",
        arguments={"action": "delete", "artifact_id": "art_test"},
    ))

    assert called == {"workspace_id": "test_ws", "artifact_id": "art_test", "hard": False}
    assert result["ok"] is True
    assert result["recoverable"] is True
    assert result["lifecycle"] == "deleted"
