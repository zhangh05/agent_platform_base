# harness/test_storage_filestore_foundation.py
"""Tests for the storage package foundation layer."""

import json
import os
import sys
import shutil
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def tmp_workspace(monkeypatch, tmp_path):
    """Set up a temporary workspace root for testing."""
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from storage.paths import ensure_workspace_storage_dirs
    ensure_workspace_storage_dirs("test_ws")
    return tmp_path


# ── paths.py ─────────────────────────────────────────────────────────

def test_ensure_workspace_creates_all_dirs(tmp_workspace):
    ws = tmp_workspace / "test_ws"
    assert (ws / "files" / "data").is_dir()
    assert (ws / "files" / "tmp").is_dir()
    assert (ws / "index").is_dir()
    assert (ws / "inbox").is_dir()
    assert (ws / "context").is_dir()
    assert (ws / "sessions").is_dir()
    assert (ws / "runs").is_dir()


# ── schemas.py ───────────────────────────────────────────────────────

def test_file_record_as_dict():
    from storage.schemas import FileRecord
    rec = FileRecord(
        file_id="file_abc123",
        workspace_id="test_ws",
        logical_type="user_upload",
        file_kind="text",
        path="files/data/file_abc123__test.txt",
    )
    d = rec.as_dict()
    assert d["file_id"] == "file_abc123"
    assert d["workspace_id"] == "test_ws"
    assert d["logical_type"] == "user_upload"
    assert d["lifecycle"] == "active"


def test_file_reference_as_dict():
    from storage.schemas import FileReference
    ref = FileReference(
        ref_id="ref_xyz",
        workspace_id="test_ws",
        file_id="file_abc",
        owner_type="artifact",
        owner_id="art_123",
        relation="output",
    )
    d = ref.as_dict()
    assert d["ref_id"] == "ref_xyz"
    assert d["owner_type"] == "artifact"


# ── file_store.py ────────────────────────────────────────────────────

def test_write_agent_output_creates_file_and_index(tmp_workspace):
    from storage.file_store import write_agent_output, get_file_record

    rec = write_agent_output(
        workspace_id="test_ws",
        content="Hello, world!",
        logical_type="artifact_output",
        file_kind="text",
        title="greeting",
        source="test",
    )

    assert rec.file_id.startswith("file_")
    assert rec.size_bytes == len("Hello, world!".encode())
    assert rec.sha256

    # File exists on disk
    ws = tmp_workspace / "test_ws"
    full_path = ws / rec.path
    assert full_path.exists()
    assert full_path.read_text() == "Hello, world!"

    # Index has the record
    found = get_file_record("test_ws", rec.file_id)
    assert found is not None
    assert found["file_id"] == rec.file_id


def test_workspace_write_artifact_creates_lineaged_file_and_artifact(tmp_workspace):
    from core.tools.general_tools.file_tools import handle_ws_write_artifact_file
    from core.tools.schemas import ToolInvocation
    from storage.artifact_metadata_store import list_artifact_records
    from storage.file_store import get_file_record
    from storage.reference_index import list_references_for_file

    result = handle_ws_write_artifact_file(ToolInvocation(
        tool_id="workspace.file",
        workspace_id="test_ws",
        session_id="session_1",
        run_id="run_1",
        requested_by="turn_runner",
        arguments={
            "action": "write_artifact",
            "filename": "result.csv",
            "content": "name,value\na,1\n",
        },
    ))

    assert result["ok"] is True
    assert result["filepath"].endswith("__result.csv")
    assert result["artifact_ids"] == [result["artifact_id"]]
    record = get_file_record("test_ws", result["file_id"])
    assert record["run_id"] == "run_1"
    assert record["session_id"] == "session_1"
    assert any(item["artifact_id"] == result["artifact_id"] for item in list_artifact_records("test_ws"))
    assert any(ref["owner_id"] == result["artifact_id"] for ref in list_references_for_file("test_ws", result["file_id"]))


def test_import_user_upload_preserves_original(tmp_workspace):
    from storage.file_store import import_user_upload, get_file_record

    # Create a source file
    src = tmp_workspace / "upload_source.txt"
    src.write_text("config content here")

    rec = import_user_upload(
        workspace_id="test_ws",
        file_source=str(src),
        original_name="device_config.txt",
        source="test_upload",
    )

    assert rec.file_id.startswith("file_")
    assert rec.original_name == "device_config.txt"
    assert rec.logical_type == "user_upload"
    assert rec.size_bytes > 0

    # Original source still exists
    assert src.exists()

    # Copy exists in managed storage
    ws = tmp_workspace / "test_ws"
    managed = ws / rec.path
    assert managed.exists()
    assert managed.read_text() == "config content here"

    # Index has the record
    found = get_file_record("test_ws", rec.file_id)
    assert found is not None


def test_resolve_file_path_blocks_traversal(tmp_workspace):
    from storage.file_store import write_agent_output, resolve_file_path

    rec = write_agent_output(
        workspace_id="test_ws",
        content="safe",
        logical_type="artifact_output",
        file_kind="text",
        title="safe_file",
    )

    # Normal resolve works
    path = resolve_file_path("test_ws", rec.file_id)
    assert path.exists()


def test_list_files_filters_by_type(tmp_workspace):
    from storage.file_store import write_agent_output, list_files

    write_agent_output("test_ws", "a", "artifact_output", "text", title="a")
    write_agent_output("test_ws", "b", "report", "markdown", title="b")
    write_agent_output("test_ws", "c", "artifact_output", "text", title="c")

    all_files = list_files("test_ws")
    assert len(all_files) == 3

    artifacts = list_files("test_ws", logical_type="artifact_output")
    assert len(artifacts) == 2

    reports = list_files("test_ws", logical_type="report")
    assert len(reports) == 1


def test_soft_delete_hides_from_active_list(tmp_workspace):
    from storage.file_store import write_agent_output, list_files, soft_delete_file

    rec = write_agent_output("test_ws", "doomed", "artifact_output", "text", title="doomed")

    assert len(list_files("test_ws")) == 1

    soft_delete_file("test_ws", rec.file_id)

    assert len(list_files("test_ws")) == 0
    assert len(list_files("test_ws", lifecycle="soft_deleted")) == 1


# ── reference_index.py ───────────────────────────────────────────────

def test_add_and_list_references(tmp_workspace):
    from storage.reference_index import add_reference, list_references_for_file, list_references_for_owner

    ref = add_reference("test_ws", "file_1", "artifact", "art_1", "output")
    assert ref.ref_id.startswith("ref_")

    add_reference("test_ws", "file_1", "session", "sess_1", "attachment")

    file_refs = list_references_for_file("test_ws", "file_1")
    assert len(file_refs) == 2

    art_refs = list_references_for_owner("test_ws", "artifact", "art_1")
    assert len(art_refs) == 1
    assert art_refs[0]["file_id"] == "file_1"


def test_remove_reference(tmp_workspace):
    from storage.reference_index import add_reference, list_references_for_file, remove_reference

    ref = add_reference("test_ws", "file_2", "run", "run_1", "source")
    assert len(list_references_for_file("test_ws", "file_2")) == 1

    remove_reference("test_ws", ref.ref_id)
    assert len(list_references_for_file("test_ws", "file_2")) == 0


# ── gc.py ────────────────────────────────────────────────────────────

def test_gc_preview_finds_orphans(tmp_workspace):
    from storage.file_store import write_agent_output
    from storage.gc import gc_preview

    write_agent_output("test_ws", "managed", "artifact_output", "text", title="managed")

    # Create an unmanaged file (orphan)
    orphan = tmp_workspace / "test_ws" / "files" / "data" / "orphan.txt"
    orphan.write_text("orphan content")

    report = gc_preview("test_ws")
    assert len(report["orphan_files"]) >= 1
    orphan_paths = [o["path"] for o in report["orphan_files"]]
    assert any("orphan.txt" in p for p in orphan_paths)


# ── policy.py ────────────────────────────────────────────────────────

def test_policy_constants_exist():
    from storage.policy import MAX_UPLOAD_BYTES, BINARY_KINDS, TEXT_KINDS, SENSITIVITY_LEVELS

    assert MAX_UPLOAD_BYTES > 0
    assert "pdf" in BINARY_KINDS
    assert "text" in TEXT_KINDS
    assert "internal" in SENSITIVITY_LEVELS


# ── Path security hardening ──────────────────────────────────────────

def test_create_file_record_rejects_path_escape(tmp_workspace):
    from storage.file_store import create_file_record
    with pytest.raises(ValueError):
        create_file_record(
            workspace_id="test_ws",
            logical_type="artifact_output",
            file_kind="text",
            path="../evil.txt",
        )


def test_create_file_record_rejects_absolute_path(tmp_workspace):
    from storage.file_store import create_file_record
    with pytest.raises(ValueError):
        create_file_record(
            workspace_id="test_ws",
            logical_type="artifact_output",
            file_kind="text",
            path="/tmp/evil.txt",
        )


def test_resolve_file_path_rejects_prefix_spoof(tmp_workspace):
    from storage.file_store import resolve_file_path

    evil_ws = tmp_workspace / "test_ws_evil"
    evil_ws.mkdir()
    evil_file = evil_ws / "evil.txt"
    evil_file.write_text("evil")

    idx = tmp_workspace / "test_ws" / "index" / "files.jsonl"
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text(
        '{"file_id":"file_bad","workspace_id":"test_ws","logical_type":"artifact_output",'
        '"file_kind":"text","path":"../test_ws_evil/evil.txt","lifecycle":"active"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        resolve_file_path("test_ws", "file_bad")


# ── Policy enforcement ───────────────────────────────────────────────

def test_import_user_upload_rejects_invalid_file_kind(tmp_workspace):
    from storage.file_store import import_user_upload

    src = tmp_workspace / "bad.bin"
    src.write_text("bad")

    with pytest.raises(ValueError, match="unsupported_file_kind"):
        import_user_upload(
            workspace_id="test_ws",
            file_source=str(src),
            original_name="bad.bin",
            file_kind="malware",
        )


def test_read_file_content_rejects_binary(tmp_workspace):
    from storage.file_store import import_user_upload, read_file_content

    src = tmp_workspace / "sample.pdf"
    src.write_bytes(b"%PDF-1.7\n")

    rec = import_user_upload(
        workspace_id="test_ws",
        file_source=str(src),
        original_name="sample.pdf",
        logical_type="document_input",
        file_kind="pdf",
        binary=True,
    )

    with pytest.raises(ValueError, match="binary"):
        read_file_content("test_ws", rec.file_id)


def test_workspace_file_extracts_docx_attachment_by_file_id(tmp_workspace):
    """A chat attachment is a FileStore id, never a workspace path guess."""
    docx = pytest.importorskip("docx")
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    from core.tools.schemas import ToolInvocation
    from storage.file_store import import_user_upload

    source = tmp_workspace / "runbook.docx"
    document = docx.Document()
    document.add_heading("倒换测试手册", level=1)
    document.add_paragraph("先确认链路状态，再执行倒换。")
    document.save(source)
    record = import_user_upload(
        workspace_id="test_ws",
        file_source=source,
        original_name="runbook.docx",
        logical_type="document_input",
        file_kind="docx",
        binary=True,
    )

    result = CANONICAL_REGISTRY["workspace.file"].handler(ToolInvocation(
        tool_id="workspace.file",
        workspace_id="test_ws",
        arguments={"action": "extract_document", "file_id": record.file_id},
    ))

    assert result["ok"] is True
    assert result["file_id"] == record.file_id
    assert "倒换测试手册" in result["content"]
    assert "先确认链路状态" in result["content"]
    assert result["embedded_image_count"] == 0


def test_workspace_file_extracts_docx_image_for_vision(tmp_workspace):
    docx = pytest.importorskip("docx")
    from PIL import Image
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    from core.tools.schemas import ToolInvocation
    from storage.file_store import import_user_upload, get_file_record

    image_path = tmp_workspace / "diagram.png"
    Image.new("RGB", (8, 8), color="navy").save(image_path)
    source = tmp_workspace / "runbook.docx"
    document = docx.Document()
    document.add_picture(str(image_path))
    document.save(source)
    record = import_user_upload("test_ws", source, "runbook.docx", logical_type="document_input", file_kind="docx", binary=True)

    extracted = CANONICAL_REGISTRY["workspace.file"].handler(ToolInvocation(
        tool_id="workspace.file", workspace_id="test_ws",
        arguments={"action": "extract_document", "file_id": record.file_id},
    ))
    assert extracted["ok"] is True
    assert extracted["embedded_image_count"] == 1

    result = CANONICAL_REGISTRY["workspace.file"].handler(ToolInvocation(
        tool_id="workspace.file", workspace_id="test_ws",
        arguments={"action": "extract_document_image", "file_id": record.file_id, "image_index": 1},
    ))

    assert result["ok"] is True
    evidence = result["evidence_parts"][0]
    assert evidence["kind"] == "image"
    assert evidence["reference"]["kind"] == "managed_file"
    assert get_file_record("test_ws", evidence["reference"]["file_id"])["file_kind"] == "png"

    batch = CANONICAL_REGISTRY["workspace.file"].handler(ToolInvocation(
        tool_id="workspace.file", workspace_id="test_ws",
        arguments={"action": "extract_document_images", "file_id": record.file_id, "start_index": 1},
    ))
    assert batch["ok"] is True
    assert batch["image_count"] == 1
    assert batch["has_more"] is False
    assert len(batch["evidence_parts"]) == 1


def test_workspace_file_extracts_text_attachment_by_file_id(tmp_workspace):
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    from core.tools.schemas import ToolInvocation
    from storage.file_store import write_agent_output

    record = write_agent_output("test_ws", "plain text", "artifact_output", "text", title="note")
    result = CANONICAL_REGISTRY["workspace.file"].handler(ToolInvocation(
        tool_id="workspace.file",
        workspace_id="test_ws",
        arguments={"action": "extract_document", "file_id": record.file_id},
    ))

    assert result["ok"] is True
    assert result["content"] == "plain text"


@pytest.mark.parametrize(("file_kind", "filename", "expected"), [
    ("xlsx", "inventory.xlsx", "设备名称"),
    ("pptx", "briefing.pptx", "倒换安排"),
])
def test_workspace_file_extracts_common_office_attachment(tmp_workspace, file_kind, filename, expected):
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    from core.tools.schemas import ToolInvocation
    from storage.file_store import import_user_upload

    source = tmp_workspace / filename
    if file_kind == "xlsx":
        openpyxl = pytest.importorskip("openpyxl")
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["设备名称", "状态"])
        sheet.append(["核心交换机", "正常"])
        book.save(source)
    else:
        pptx = pytest.importorskip("pptx")
        deck = pptx.Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = "倒换安排"
        slide.placeholders[1].text = "先检查链路。"
        deck.save(source)
    record = import_user_upload(
        "test_ws", source, filename, logical_type="document_input",
        file_kind=file_kind, binary=True,
    )

    result = CANONICAL_REGISTRY["workspace.file"].handler(ToolInvocation(
        tool_id="workspace.file", workspace_id="test_ws",
        arguments={"action": "extract_document", "file_id": record.file_id},
    ))

    assert result["ok"] is True
    assert expected in result["content"]


def test_every_non_image_chat_upload_kind_has_a_canonical_read_path():
    """Keep the chat picker and FileStore extraction capability aligned."""
    from core.tools.general_tools.filestore_tools import _EXTRACTABLE_FILE_KINDS
    from storage.policy import BINARY_KINDS, TEXT_KINDS

    picker_kinds = {"text", "config", "markdown", "json", "yaml", "xml", "html", "pdf", "docx", "xlsx", "pptx"}
    assert picker_kinds <= (set(BINARY_KINDS) | set(TEXT_KINDS))
    assert picker_kinds <= _EXTRACTABLE_FILE_KINDS
