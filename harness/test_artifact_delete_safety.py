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


def test_soft_deleted_artifact_does_not_soft_delete_its_filestore_payload(monkeypatch):
    import artifacts.store as artifact_store
    from artifacts.schemas import ArtifactRecord
    from storage import file_store

    record = ArtifactRecord(
        artifact_id="art_test", workspace_id="test_ws", file_id="file_shared",
        lifecycle="active",
    )
    monkeypatch.setattr(artifact_store, "get_artifact", lambda *_args: record)
    monkeypatch.setattr(artifact_store, "_remove_from_knowledge_index", lambda *_args: None)
    monkeypatch.setattr(artifact_store, "_save_artifact_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(file_store, "soft_delete_file", lambda *_args: (_ for _ in ()).throw(AssertionError("payload must remain active")))

    assert artifact_store.delete_artifact("test_ws", "art_test", hard=False) is True
    assert record.lifecycle == "deleted"
