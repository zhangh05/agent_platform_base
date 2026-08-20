from __future__ import annotations


def test_sensitive_artifact_isolated_by_storage_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))

    from artifacts.store import get_artifact, read_artifact_content, save_artifact
    from storage.principal import storage_principal
    from storage.workspace_store import ensure_workspace

    workspace_id = "shared"
    content = "alice private topology evidence"
    with storage_principal("alice"):
        ensure_workspace(workspace_id)
        artifact = save_artifact(
            workspace_id,
            content=content,
            artifact_type="report",
            sensitivity="sensitive",
            scope="workspace",
        )
        assert artifact is not None
        artifact_id = artifact.artifact_id
        assert read_artifact_content(workspace_id, artifact_id, allow_sensitive=True) == content

    with storage_principal("bob"):
        assert get_artifact(workspace_id, artifact_id) is None
        assert read_artifact_content(workspace_id, artifact_id, allow_sensitive=True) is None
