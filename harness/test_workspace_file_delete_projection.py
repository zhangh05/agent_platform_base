"""Regression: canonical workspace deletes must project managed file lifecycle."""


def test_workspace_file_delete_soft_deletes_matching_managed_file(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from core.tools.canonical_registry import _local_delete
    from core.tools.schemas import ToolInvocation
    from storage.data_management import managed_data_files
    from storage.file_store import get_file_record
    from core.tools.general_tools.file_tools import handle_ws_write_artifact_file

    created = handle_ws_write_artifact_file(ToolInvocation(
        tool_id="workspace.file", workspace_id="test_ws", session_id="session_1",
        run_id="run_1", requested_by="turn_runner",
        arguments={"action": "write_artifact", "filename": "delete_probe.md", "content": "delete projection probe"},
    ))
    assert created["ok"] is True
    filepath = created["filepath"]
    file_id = created["file_id"]
    assert any(item["file_id"] == file_id for item in managed_data_files("test_ws"))

    deleted = _local_delete(ToolInvocation(
        tool_id="workspace.file", workspace_id="test_ws", session_id="session_1",
        run_id="run_2", requested_by="turn_runner",
        arguments={"action": "delete", "filepath": filepath},
    ))

    assert deleted["ok"] is True
    assert deleted["deleted"] is True
    record = get_file_record("test_ws", file_id)
    assert record is not None
    assert record["lifecycle"] == "soft_deleted"
    assert record["metadata"]["deleted_at"]
    assert record["metadata"]["trash_path"] == deleted["trash_path"]
    assert record["metadata"]["deleted_run_id"] == "run_2"
    assert record["metadata"]["deleted_session_id"] == "session_1"
    assert not any(item["file_id"] == file_id for item in managed_data_files("test_ws"))


def test_workspace_file_delete_rejects_ambiguous_managed_path_before_move(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from core.tools.canonical_registry import _local_delete
    from core.tools.schemas import ToolInvocation
    from storage.file_store import create_file_record
    from storage.paths import ensure_workspace_storage_dirs, workspace_root

    ensure_workspace_storage_dirs("test_ws")
    root = workspace_root("test_ws")
    path = root / "files" / "data" / "ambiguous.txt"
    path.write_text("ambiguous", encoding="utf-8")
    create_file_record("test_ws", "artifact_output", "txt", "files/data/ambiguous.txt", file_id="file_ambiguous_1")
    create_file_record("test_ws", "artifact_output", "txt", "files/data/ambiguous.txt", file_id="file_ambiguous_2")

    result = _local_delete(ToolInvocation(
        tool_id="workspace.file", workspace_id="test_ws", session_id="session_1",
        run_id="run_1", requested_by="turn_runner",
        arguments={"action": "delete", "filepath": "files/data/ambiguous.txt"},
    ))

    assert result["ok"] is False
    assert result["error"] == "managed_file_index_ambiguous"
    assert path.exists()


def test_workspace_file_delete_restores_payload_when_lifecycle_sync_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from core.tools.canonical_registry import _local_delete
    from core.tools.schemas import ToolInvocation
    from storage.file_store import create_file_record, get_file_record
    from storage.paths import ensure_workspace_storage_dirs, workspace_root

    ensure_workspace_storage_dirs("test_ws")
    root = workspace_root("test_ws")
    path = root / "files" / "data" / "sync-fail.txt"
    path.write_text("must remain active", encoding="utf-8")
    create_file_record("test_ws", "artifact_output", "txt", "files/data/sync-fail.txt", file_id="file_sync_fail")

    monkeypatch.setattr("storage.file_store.soft_delete_file", lambda *_: False)
    result = _local_delete(ToolInvocation(
        tool_id="workspace.file", workspace_id="test_ws", session_id="session_1",
        run_id="run_1", requested_by="turn_runner",
        arguments={"action": "delete", "filepath": "files/data/sync-fail.txt"},
    ))

    assert result["ok"] is False
    assert result["error"] == "managed_file_lifecycle_sync_failed"
    assert path.exists()
    assert get_file_record("test_ws", "file_sync_fail")["lifecycle"] == "active"


def test_soft_delete_file_propagates_index_update_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from storage.file_store import create_file_record, get_file_record, soft_delete_file
    from storage.paths import ensure_workspace_storage_dirs, workspace_root

    ensure_workspace_storage_dirs("test_ws")
    path = workspace_root("test_ws") / "files" / "data" / "index-fail.txt"
    path.write_text("index failure", encoding="utf-8")
    create_file_record(
        "test_ws", "artifact_output", "txt", "files/data/index-fail.txt",
        file_id="file_index_fail",
    )
    monkeypatch.setattr("storage.file_store.index.update_file_record", lambda *_: False)

    assert soft_delete_file("test_ws", "file_index_fail") is False
    assert get_file_record("test_ws", "file_index_fail")["lifecycle"] == "active"
