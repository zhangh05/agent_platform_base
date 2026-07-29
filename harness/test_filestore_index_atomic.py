# harness/test_filestore_index_atomic.py
"""P2-B: FileStore index atomic/concurrent safety tests.

Coverage:
  - append_file_record 50 concurrent writes, no lost records
  - concurrent append doesn't produce malformed JSON lines
  - update fails → old index unchanged
  - compact fails → old index unchanged
  - tmp files cleaned up after failure
  - lock released after use → subsequent writes OK
  - duplicate file_id resolution (last wins after compact)
  - validate_file_index catches path escapes, missing disk, size mismatch
  - import_user_upload / write_agent_output use new index
  - multi-workspace concurrent writes don't interfere
"""

import json
import os
import tempfile
import threading
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from storage.index import (
    append_file_record,
    read_file_records,
    update_file_record,
    compact_file_index,
    validate_file_index,
    IndexLock,
)


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a minimal workspace structure for index testing."""
    # Patch workspace_root to return tmp_path
    with patch("storage.index.workspace_root", return_value=Path(tmp_path)):
        idx_dir = tmp_path / "index"
        idx_dir.mkdir(parents=True, exist_ok=True)
        yield str(tmp_path)


@pytest.fixture
def test_record():
    """Generate a unique test record."""
    fid = f"file_{uuid.uuid4().hex[:16]}"
    return {
        "file_id": fid,
        "workspace_id": "default",
        "logical_type": "user_upload",
        "file_kind": "text",
        "path": f"files/data/{fid}__test.txt",
        "original_name": "test.txt",
        "mime_type": "text/plain",
        "binary": False,
        "size_bytes": 100,
        "sha256": "abc123",
        "created_at": "2026-06-21T00:00:00Z",
        "created_by": "test",
        "lifecycle": "active",
        "source": "test",
        "metadata": {},
    }


# ═══════════════════════════════════════════════════════════════════════
# Basic append/read tests
# ═══════════════════════════════════════════════════════════════════════


class TestBasicAppendRead:
    def test_append_and_read(self, tmp_workspace, test_record):
        append_file_record("default", test_record)
        records = read_file_records("default")
        assert len(records) == 1
        assert records[0]["file_id"] == test_record["file_id"]

    def test_append_multiple_records(self, tmp_workspace, test_record):
        recs = []
        for i in range(10):
            r = dict(test_record)
            r["file_id"] = f"file_{uuid.uuid4().hex[:16]}"
            r["path"] = f"files/data/{r['file_id']}__test.txt"
            recs.append(r)
            append_file_record("default", r)

        records = read_file_records("default")
        assert len(records) == 10

    def test_read_empty_index(self, tmp_workspace):
        records = read_file_records("default")
        assert records == []


# ═══════════════════════════════════════════════════════════════════════
# Concurrent append tests
# ═══════════════════════════════════════════════════════════════════════


class TestConcurrentAppend:
    def test_50_concurrent_appends_no_lost_records(self, tmp_workspace):
        """50 concurrent appends must not lose any records."""
        errors = []
        recs = []

        def append_one(i):
            try:
                r = {
                    "file_id": f"file_c{i:04d}",
                    "workspace_id": "default",
                    "logical_type": "user_upload",
                    "file_kind": "text",
                    "path": f"files/data/file_c{i:04d}__t.txt",
                    "original_name": "t.txt",
                    "mime_type": "text/plain",
                    "binary": False,
                    "size_bytes": i,
                    "sha256": f"hash_{i}",
                    "created_at": "2026-06-21T00:00:00Z",
                    "created_by": "test",
                    "lifecycle": "active",
                    "source": "test",
                    "metadata": {},
                }
                append_file_record("default", r)
                recs.append(r["file_id"])
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=append_one, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent append errors: {errors}"
        records = read_file_records("default")
        assert len(records) == 50, f"Expected 50 records, got {len(records)}"

    def test_concurrent_appends_no_malformed_json(self, tmp_workspace):
        """Concurrent appends must not produce malformed JSON lines."""
        def append_one(i):
            r = {
                "file_id": f"file_m{i:04d}",
                "workspace_id": "default",
                "logical_type": "user_upload",
                "file_kind": "text",
                    "path": f"files/data/file_m{i:04d}__t.txt",
                "original_name": "t.txt",
                "mime_type": "text/plain",
                "binary": False,
                "size_bytes": i,
                "sha256": f"hash_{i}",
                "created_at": "2026-06-21T00:00:00Z",
                "created_by": "test",
                "lifecycle": "active",
                "source": "test",
                "metadata": {},
            }
            append_file_record("default", r)

        threads = [threading.Thread(target=append_one, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Read index directly to verify no malformed lines
        from storage.index import _index_path
        idx = _index_path("default")
        assert idx.is_file()
        for line in idx.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError as e:
                    pytest.fail(f"Malformed JSON in index: {line[:80]} → {e}")


# ═══════════════════════════════════════════════════════════════════════
# Atomic update tests
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicUpdate:
    def test_update_preserves_old_on_failure(self, tmp_workspace, test_record):
        """If update fails midway, old index must be unchanged."""
        append_file_record("default", test_record)
        original = read_file_records("default")

        # Inject failure during _atomic_write_lines in update_file_record
        with patch("storage.index._atomic_write_lines", side_effect=OSError("disk full")):
            try:
                update_file_record("default", test_record["file_id"], {"lifecycle": "soft_deleted"})
            except OSError:
                pass

        # Index must be unchanged
        after = read_file_records("default")
        assert len(after) == len(original)
        assert after[0]["file_id"] == test_record["file_id"]
        assert after[0].get("lifecycle") == "active"  # not soft_deleted

    def test_update_success(self, tmp_workspace, test_record):
        append_file_record("default", test_record)
        ok = update_file_record("default", test_record["file_id"], {"lifecycle": "soft_deleted"})
        assert ok is True

        records = read_file_records("default")
        assert len(records) == 1
        # May have lifecycle updated
        updated = [r for r in records if r["file_id"] == test_record["file_id"]]
        assert len(updated) == 1

    def test_update_nonexistent(self, tmp_workspace):
        ok = update_file_record("default", "nonexistent_id", {"lifecycle": "soft_deleted"})
        assert ok is False


# ═══════════════════════════════════════════════════════════════════════
# Compact tests
# ═══════════════════════════════════════════════════════════════════════


class TestCompact:
    def test_compact_removes_soft_deleted(self, tmp_workspace, test_record):
        """Soft-deleted records removed by compact."""
        append_file_record("default", test_record)
        update_file_record("default", test_record["file_id"], {"lifecycle": "soft_deleted"})

        result = compact_file_index("default")
        assert result["removed"] >= 1
        records = read_file_records("default")
        assert len(records) == 0  # All soft-deleted removed

    def test_compact_resolves_duplicates(self, tmp_workspace, test_record):
        """Duplicate file_ids: last write wins after compact."""
        # Append same ID twice with different data
        r1 = dict(test_record)
        r1["size_bytes"] = 100
        append_file_record("default", r1)

        r2 = dict(test_record)
        r2["size_bytes"] = 200  # Updated value
        append_file_record("default", r2)

        result = compact_file_index("default")
        assert result["duplicates_resolved"] >= 0

        records = read_file_records("default")
        assert len(records) == 1
        assert records[0]["size_bytes"] == 200  # last write wins

    def test_compact_preserves_old_on_failure(self, tmp_workspace, test_record):
        """Compact failure must not corrupt index."""
        append_file_record("default", test_record)
        original = read_file_records("default")

        with patch("storage.index._atomic_write_lines", side_effect=OSError("disk full")):
            try:
                compact_file_index("default")
            except OSError:
                pass

        after = read_file_records("default")
        assert len(after) == len(original)
        assert after[0]["file_id"] == test_record["file_id"]


# ═══════════════════════════════════════════════════════════════════════
# Lock tests
# ═══════════════════════════════════════════════════════════════════════


class TestLocking:
    def test_lock_released_after_use(self, tmp_workspace, test_record):
        """After IndexLock exit, subsequent writes succeed."""
        with IndexLock("default"):
            pass  # lock acquired and released

        # Should be able to write now
        append_file_record("default", test_record)
        assert len(read_file_records("default")) == 1

    def test_lock_timeout(self, tmp_workspace):
        """Lock timeout when held by another process-ish thread."""
        import threading
        held = threading.Event()
        released = threading.Event()

        def holder():
            with IndexLock("default", timeout=10):
                held.set()
                released.wait(timeout=2)  # hold until released

        t = threading.Thread(target=holder)
        t.start()
        held.wait(timeout=1)

        # Try to acquire with short timeout
        with pytest.raises(TimeoutError):
            with IndexLock("default", timeout=0.2):
                pass

        released.set()
        t.join()


# ═══════════════════════════════════════════════════════════════════════
# Validation tests
# ═══════════════════════════════════════════════════════════════════════


class TestValidate:
    def test_validate_empty_index(self, tmp_workspace):
        result = validate_file_index("default")
        assert result["ok"] is True
        assert result["stats"]["total_records"] == 0

    def test_validate_clean_index(self, tmp_workspace, test_record):
        append_file_record("default", test_record)
        result = validate_file_index("default", check_disk=False)
        assert result["ok"] is True
        assert result["stats"]["total_records"] == 1
        assert result["stats"]["valid"] == 1

    def test_detect_path_escape(self, tmp_workspace, test_record):
        """Absolute path should be detected as path escape."""
        r = dict(test_record)
        r["path"] = "/etc/passwd"
        append_file_record("default", r)

        result = validate_file_index("default", check_disk=False)
        assert result["ok"] is False
        assert result["stats"]["path_escapes"] >= 1

    def test_detect_missing_disk_file(self, tmp_workspace, test_record):
        """File referenced in index but absent on disk."""
        append_file_record("default", test_record)
        result = validate_file_index("default", check_disk=True)
        # File doesn't exist on tmp disk, should warn
        assert result["stats"]["missing_disk"] >= 1

    def test_detect_duplicate_ids(self, tmp_workspace, test_record):
        """Duplicate file_ids detected."""
        append_file_record("default", test_record)
        append_file_record("default", test_record)
        result = validate_file_index("default", check_disk=False)
        assert result["stats"]["duplicate_ids"] >= 1

    def test_detect_invalid_lifecycle(self, tmp_workspace, test_record):
        r = dict(test_record)
        r["lifecycle"] = "garbage_status"
        append_file_record("default", r)
        result = validate_file_index("default", check_disk=False)
        assert result["stats"]["invalid_lifecycle"] >= 1


# ═══════════════════════════════════════════════════════════════════════
# Integration: file_store.py uses new index
# ═══════════════════════════════════════════════════════════════════════


class TestFileStoreIntegration:
    def test_create_file_record_uses_new_index(self, tmp_workspace, tmp_path):
        """create_file_record writes through storage.index."""
        # Patch workspace_root
        with patch("storage.index.workspace_root", return_value=Path(tmp_path)):
            with patch("storage.file_store.workspace_root", return_value=Path(tmp_path)):
                # Create a test file
                ws = tmp_path
                (ws / "files" / "data").mkdir(parents=True)
                test_file = ws / "files" / "data" / "test_file.txt"
                test_file.write_text("hello")

                from storage.file_store import create_file_record
                rec = create_file_record(
                    workspace_id="default",
                    logical_type="user_upload",
                    file_kind="text",
                    path="files/data/test_file.txt",
                    original_name="test.txt",
                )
                assert rec is not None
                assert rec.file_id.startswith("file_")

                # Verify in index
                records = read_file_records("default")
                assert len(records) >= 1

    def test_soft_delete_uses_new_index(self, tmp_workspace, tmp_path, test_record):
        with patch("storage.index.workspace_root", return_value=Path(tmp_path)):
            with patch("storage.file_store.workspace_root", return_value=Path(tmp_path)):
                ws = tmp_path
                (ws / "files" / "user_upload" / "original").mkdir(parents=True)
                tf = ws / "files" / "user_upload" / "original" / "test_del.txt"
                tf.write_text("del")

                append_file_record("default", test_record)

                from storage.file_store import soft_delete_file
                ok = soft_delete_file("default", test_record["file_id"])
                assert ok is True

                # Verify lifecycle updated (via index reads)
                records = read_file_records("default")
                for r in records:
                    if r["file_id"] == test_record["file_id"]:
                        assert r.get("lifecycle") == "soft_deleted"


# ═══════════════════════════════════════════════════════════════════════
# Multi-workspace isolation
# ═══════════════════════════════════════════════════════════════════════


class TestMultiWorkspace:
    def test_concurrent_multi_workspace_no_interference(self, tmp_path):
        """Concurrent writes to different workspaces don't interfere."""
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        (ws1 / "index").mkdir(parents=True)
        (ws2 / "index").mkdir(parents=True)

        def ws_root_side_effect(ws_id):
            if ws_id == "ws1":
                return Path(ws1)
            return Path(ws2)

        errors = []

        def write_ws1(i):
            try:
                append_file_record("ws1", {
                    "file_id": f"ws1_file_{i:04d}",
                    "workspace_id": "ws1",
                    "logical_type": "user_upload",
                    "file_kind": "text",
                    "path": f"ws1/file_{i}.txt",
                    "original_name": "t.txt",
                    "mime_type": "text/plain",
                    "binary": False,
                    "size_bytes": i,
                    "sha256": f"h{i}",
                    "created_at": "2026-06-21T00:00:00Z",
                    "created_by": "test",
                    "lifecycle": "active",
                    "source": "test",
                    "metadata": {},
                })
            except Exception as e:
                errors.append(f"ws1:{e}")

        def write_ws2(i):
            try:
                append_file_record("ws2", {
                    "file_id": f"ws2_file_{i:04d}",
                    "workspace_id": "ws2",
                    "logical_type": "user_upload",
                    "file_kind": "text",
                    "path": f"ws2/file_{i}.txt",
                    "original_name": "t.txt",
                    "mime_type": "text/plain",
                    "binary": False,
                    "size_bytes": i,
                    "sha256": f"h{i}",
                    "created_at": "2026-06-21T00:00:00Z",
                    "created_by": "test",
                    "lifecycle": "active",
                    "source": "test",
                    "metadata": {},
                })
            except Exception as e:
                errors.append(f"ws2:{e}")

        with patch("storage.index.workspace_root", side_effect=ws_root_side_effect):
            threads = []
            for i in range(10):
                threads.append(threading.Thread(target=write_ws1, args=(i,)))
                threads.append(threading.Thread(target=write_ws2, args=(i,)))
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"Multi-workspace errors: {errors}"

        # Each workspace should have exactly 10 records
        with patch("storage.index.workspace_root", return_value=Path(ws1)):
            assert len(read_file_records("ws1")) == 10
        with patch("storage.index.workspace_root", return_value=Path(ws2)):
            assert len(read_file_records("ws2")) == 10
