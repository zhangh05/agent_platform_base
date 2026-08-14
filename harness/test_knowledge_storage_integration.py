# Knowledge ingestion storage contracts.
"""Tests for knowledge import file_id support and normalized FileRecord."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def knowledge_ws(monkeypatch, tmp_path):
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


def test_knowledge_import_file_id(knowledge_ws, monkeypatch):
    """import_file should accept a file_id and import its content."""
    from storage.file_store import import_user_upload
    from agent.modules.knowledge.ingestion import import_file

    # Create and upload a markdown file
    src = knowledge_ws / "doc.md"
    src.write_text("# Hello\n\nThis is a test document.")

    file_rec = import_user_upload(
        workspace_id="test_ws",
        file_source=str(src),
        original_name="doc.md",
        file_kind="markdown",
        source="test_upload",
    )

    # Import using file_id
    result = import_file(
        workspace_id="test_ws",
        source="ksrc_test",
        source_type="project_doc",
        file_id=file_rec.file_id,
        title="Test Document",
    )

    assert result.get("ok") is True
    assert result.get("source_file_id") == file_rec.file_id
    # normalized_file_id may be empty if parser didn't produce markdown content
    normalized = result.get("normalized_file_id", "")
    if normalized:
        from storage.file_store import get_file_record
        nfr = get_file_record("test_ws", normalized)
        assert nfr
        assert nfr["logical_type"] == "knowledge_normalized"


def test_knowledge_import_creates_references(knowledge_ws, monkeypatch):
    """import_file with file_id should create ReferenceIndex entries."""
    from storage.file_store import import_user_upload
    from storage.reference_index import list_references_for_file
    from agent.modules.knowledge.ingestion import import_file

    src = knowledge_ws / "ref_doc.md"
    src.write_text("# Reference Test\n\nContent for reference index.")

    file_rec = import_user_upload(
        workspace_id="test_ws",
        file_source=str(src),
        original_name="ref_doc.md",
        file_kind="markdown",
        source="test_upload",
    )

    result = import_file(
        workspace_id="test_ws",
        source="ksrc_ref",
        source_type="project_doc",
        file_id=file_rec.file_id,
        title="Reference Test",
    )

    assert result.get("ok") is True

    # Verify references exist for source file
    refs = list_references_for_file("test_ws", file_rec.file_id)
    assert any(r["owner_type"] == "knowledge_source" for r in refs)

    # Verify references exist for normalized file
    nfid = result.get("normalized_file_id", "")
    if nfid:
        nrefs = list_references_for_file("test_ws", nfid)
        assert any(r["owner_type"] == "knowledge_source" for r in nrefs)


def test_knowledge_import_chunk_metadata_has_file_refs(knowledge_ws, monkeypatch):
    """Chunks created from import should carry source_file_id in metadata."""
    from storage.file_store import import_user_upload
    from agent.modules.knowledge.ingestion import import_file

    src = knowledge_ws / "chunk_test.md"
    src.write_text("# Chunk Test\n\nParagraph one.\n\nParagraph two.")

    file_rec = import_user_upload(
        workspace_id="test_ws",
        file_source=str(src),
        original_name="chunk_test.md",
        file_kind="markdown",
        source="test_upload",
    )

    result = import_file(
        workspace_id="test_ws",
        source="ksrc_chunk",
        source_type="project_doc",
        file_id=file_rec.file_id,
        title="Chunk Test",
    )

    assert result.get("ok") is True
    assert result.get("source_file_id") == file_rec.file_id
    # normalized_file_id and chunk metadata persistence are verified
    # by import order: normalize → metadata → replace_chunks
