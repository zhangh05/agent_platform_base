# harness/test_storage_gc_dry_run.py
"""GC dry-run tests — no file deletion, no index changes."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def gc_ws(monkeypatch, tmp_path):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("LZCORE_WORKSPACE_DIR", str(ws))
    try:
        import artifacts.store as _as
    except Exception:
        pass
    from storage.paths import ensure_workspace_storage_dirs
    ensure_workspace_storage_dirs("test_ws")
    return ws


def test_gc_dry_run_does_not_modify(gc_ws):
    """GC dry-run must not modify index or files."""
    from storage.file_store import write_agent_output, list_files
    from scripts.storage_gc import run_gc_dry_run

    # Create a managed file first
    rec = write_agent_output("test_ws", "gc test content", "artifact_output", "text", title="gc test")
    before = list_files("test_ws", lifecycle="")

    result = run_gc_dry_run("test_ws")
    after = list_files("test_ws", lifecycle="")

    assert result["dry_run"] is True
    assert len(before) == len(after), "GC dry-run must not modify index"
    assert result.get("orphan_files") is not None
    assert result.get("missing_files") is not None


def test_gc_dry_run_detects_orphan(gc_ws):
    """GC should detect files on disk with no FileRecord."""
    # Create an orphan file not managed by FileStore
    orphan = gc_ws / "test_ws" / "files" / "data" / "orphan.txt"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("orphan")

    from scripts.storage_gc import detect_missing_files
    missing = detect_missing_files("test_ws")
    assert any("orphan.txt" in m.get("path", "") for m in missing)
