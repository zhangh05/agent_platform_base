"""Regression: safely reconcile only legacy FileStore records proven to be in trash."""


def _legacy_trashed_record(monkeypatch, tmp_path, *, content="legacy payload"):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from storage.file_store import write_agent_output
    from storage.paths import workspace_root

    record = write_agent_output(
        "test_ws", content, logical_type="artifact_output", file_kind="markdown",
        title="legacy-delete.md", run_id="run_legacy", session_id="session_legacy",
    )
    root = workspace_root("test_ws")
    source = root / record.path
    trash = root / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    destination = trash / source.name
    source.rename(destination)
    return record, destination


def test_reconcile_trashed_file_records_previews_then_repairs_exact_payload(monkeypatch, tmp_path):
    record, trash_path = _legacy_trashed_record(monkeypatch, tmp_path)
    from storage.data_management import managed_data_files
    from storage.file_store import get_file_record, reconcile_trashed_file_records

    preview = reconcile_trashed_file_records("test_ws", apply=False)
    assert preview["repairable"] == [{"file_id": record.file_id, "trash_path": ".trash/" + trash_path.name}]
    assert preview["repaired"] == []
    assert get_file_record("test_ws", record.file_id)["lifecycle"] == "active"
    assert any(item["file_id"] == record.file_id for item in managed_data_files("test_ws"))

    repaired = reconcile_trashed_file_records("test_ws", apply=True)
    assert repaired["repaired"][0]["file_id"] == record.file_id
    current = get_file_record("test_ws", record.file_id)
    assert current["lifecycle"] == "soft_deleted"
    assert current["metadata"]["deleted_at"]
    assert current["metadata"]["trash_path"] == ".trash/" + trash_path.name
    assert current["metadata"]["reconciliation_reason"] == "verified_legacy_trash_payload"
    assert not any(item["file_id"] == record.file_id for item in managed_data_files("test_ws"))

    assert reconcile_trashed_file_records("test_ws", apply=True)["repaired"] == []


def test_reconcile_trashed_file_records_rejects_mismatched_candidate(monkeypatch, tmp_path):
    record, trash_path = _legacy_trashed_record(monkeypatch, tmp_path)
    from storage.file_store import get_file_record, reconcile_trashed_file_records

    trash_path.write_text("tampered legacy payload", encoding="utf-8")
    result = reconcile_trashed_file_records("test_ws", apply=True)

    assert result["repairable"] == []
    assert result["repaired"] == []
    assert result["skipped"] == [{"file_id": record.file_id, "reason": "trash_size_mismatch"}]
    assert get_file_record("test_ws", record.file_id)["lifecycle"] == "active"


def test_canonical_filestore_reconcile_actions_use_existing_dispatch(monkeypatch, tmp_path):
    record, _ = _legacy_trashed_record(monkeypatch, tmp_path)
    from core.tools.canonical_registry import _handle_workspace_filestore
    from core.tools.schemas import ToolInvocation
    from storage.file_store import get_file_record

    base = dict(tool_id="workspace.filestore", workspace_id="test_ws", session_id="session_legacy", run_id="run_legacy", requested_by="turn_runner")
    preview = _handle_workspace_filestore(ToolInvocation(arguments={"action": "reconcile_trash_preview"}, **base))
    assert preview["ok"] is True
    assert preview["repairable"][0]["file_id"] == record.file_id

    repaired = _handle_workspace_filestore(ToolInvocation(arguments={"action": "reconcile_trash"}, **base))
    assert repaired["ok"] is True
    assert repaired["repaired"][0]["file_id"] == record.file_id
    assert get_file_record("test_ws", record.file_id)["lifecycle"] == "soft_deleted"


def test_gc_reports_only_active_missing_payloads(monkeypatch, tmp_path):
    record, _ = _legacy_trashed_record(monkeypatch, tmp_path)
    from storage.file_store import reconcile_trashed_file_records, write_agent_output
    from storage.gc import find_missing_file_records

    assert reconcile_trashed_file_records("test_ws", apply=True)["repaired"][0]["file_id"] == record.file_id
    from storage.paths import workspace_root

    active = write_agent_output(
        "test_ws", "missing active payload", logical_type="artifact_output",
        file_kind="text", title="missing-active.txt",
    )
    (workspace_root("test_ws") / active.path).unlink()

    missing = find_missing_file_records("test_ws")
    assert [item["file_id"] for item in missing] == [active.file_id]
    assert record.file_id not in {item["file_id"] for item in missing}
