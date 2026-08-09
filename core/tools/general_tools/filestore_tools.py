# core/tools/general_tools/filestore_tools.py
"""FileStore tools - workspace.file, workspace.file, workspace.filestore, workspace.file, workspace.filestore."""

from __future__ import annotations

from core.tools.general_tools.shared import _caller_workspace, _contract, _error, _error_inv, _ok, _result, _unavailable, _workspace_path

from typing import Any
from io import BytesIO
from pathlib import Path
import zipfile


_STRUCTURED_DOCUMENT_KINDS = frozenset({"docx", "pdf", "xlsx", "pptx"})
_TEXT_ATTACHMENT_KINDS = frozenset({
    "text", "config", "markdown", "json", "yaml", "xml", "csv", "html", "log", "script", "diff",
})
_EXTRACTABLE_FILE_KINDS = _STRUCTURED_DOCUMENT_KINDS | _TEXT_ATTACHMENT_KINDS
_MAX_EXTRACT_BYTES = 100 * 1024 * 1024


def _ok(tool_id: str, **kwargs) -> dict[str, Any]:
    kwargs.setdefault("ok", True)
    kwargs.setdefault("status", "succeeded")
    kwargs.setdefault("tool_id", tool_id)
    kwargs.setdefault("summary", f"{tool_id} succeeded")
    return kwargs


def _fail(tool_id: str, error: str, **kwargs) -> dict[str, Any]:
    kwargs["ok"] = False
    kwargs.setdefault("status", "failed")
    kwargs.setdefault("tool_id", tool_id)
    kwargs["error"] = error
    kwargs.setdefault("summary", error)
    kwargs.setdefault("errors", [error])
    return kwargs


def handle_file_get(inv, *, file_id: str = "", limit: int = 50000) -> dict[str, Any]:
    """Read text content of a managed file by file_id."""
    from storage.file_store import get_file_record, read_file_content

    ws = getattr(inv, "workspace_id", None) or ""
    rec = get_file_record(ws, file_id)
    if not rec:
        return _fail("workspace.file", "file_not_found", file_id=file_id)

    if rec.get("binary"):
        return _ok("workspace.file", file_kind=rec.get("file_kind"), size_bytes=rec.get("size_bytes"),
                   sha256=rec.get("sha256"), path=rec.get("path"),
                   summary="binary file — metadata only")

    try:
        content = read_file_content(ws, file_id)
        return _ok("workspace.file", content=content[:limit],
                   size_bytes=rec.get("size_bytes"),
                   truncated=len(content) > limit, file_id=file_id)
    except Exception as exc:
        return _fail("workspace.file", str(exc)[:200], file_id=file_id)


def handle_file_preview(inv, *, file_id: str = "", limit: int = 500) -> dict[str, Any]:
    """Preview a managed file's metadata and text preview."""
    from storage.file_store import get_file_record, read_file_content

    ws = getattr(inv, "workspace_id", None) or ""
    rec = get_file_record(ws, file_id)
    if not rec:
        return _fail("workspace.file", "file_not_found", file_id=file_id)

    result = _ok("workspace.file", file_kind=rec.get("file_kind"), binary=rec.get("binary"),
                 size_bytes=rec.get("size_bytes"), sha256=rec.get("sha256"),
                 path=rec.get("path"), logical_type=rec.get("logical_type"),
                 file_id=file_id)

    if not rec.get("binary"):
        try:
            content = read_file_content(ws, file_id)
            result["preview"] = content[:limit]
            result["truncated"] = len(content) > limit
        except Exception:
            pass
    return result


def handle_file_extract_document(inv, *, file_id: str = "", limit: int = 50_000) -> dict[str, Any]:
    """Extract a managed chat attachment by FileStore id without exposing its path.

    Attachments arrive as FileStore records, not importable workspace paths.  This
    is deliberately a read-only, file-id based action so the model does not need
    to guess a path or fall back to an unrestricted command to parse a document.
    """
    from agent.modules.knowledge.parsers.base import (
        UnsupportedFormatError,
        parse_document,
    )
    from storage.file_store import get_file_record, resolve_file_path

    args = getattr(inv, "arguments", None) or {}
    file_id = file_id or str(args.get("file_id") or "")
    limit = args.get("limit", limit)
    ws = getattr(inv, "workspace_id", None) or ""
    rec = get_file_record(ws, file_id)
    if not rec:
        return _fail("workspace.file", "file_not_found", file_id=file_id)

    file_kind = str(rec.get("file_kind") or "").lower()
    if file_kind not in _EXTRACTABLE_FILE_KINDS:
        return _fail(
            "workspace.file",
            "unsupported_document_format",
            file_id=file_id,
            file_kind=file_kind,
            supported_file_kinds=sorted(_EXTRACTABLE_FILE_KINDS),
        )
    size_bytes = int(rec.get("size_bytes") or 0)
    if size_bytes > _MAX_EXTRACT_BYTES:
        return _fail(
            "workspace.file",
            "document_too_large_to_extract",
            file_id=file_id,
            size_bytes=size_bytes,
            max_size_bytes=_MAX_EXTRACT_BYTES,
        )

    try:
        raw = resolve_file_path(ws, file_id).read_bytes()
        if file_kind in _TEXT_ATTACHMENT_KINDS:
            content = raw.decode("utf-8", errors="replace")
            warnings: list[str] = []
            title = str(rec.get("original_name") or file_id)
        else:
            document = parse_document(
                raw,
                fmt=file_kind,
                title=str(rec.get("original_name") or file_id),
                source_type="attachment",
                metadata={"file_id": file_id},
            )
            content = document.normalized_markdown or ""
            warnings = list(document.warnings or [])
            title = document.title or rec.get("original_name")
    except UnsupportedFormatError as exc:
        return _fail("workspace.file", "unsupported_document_format", file_id=file_id, detail=str(exc))
    except Exception as exc:
        return _fail("workspace.file", "document_extract_failed", file_id=file_id, detail=str(exc)[:200])

    if not content.strip():
        return _fail(
            "workspace.file",
            "document_has_no_extractable_text",
            file_id=file_id,
            file_kind=file_kind,
            warnings=warnings,
        )

    bounded_limit = max(1, min(int(limit or 50_000), 50_000))
    return _ok(
        "workspace.file",
        file_id=file_id,
        file_kind=file_kind,
        title=title,
        content=content[:bounded_limit],
        size_bytes=size_bytes,
        truncated=len(content) > bounded_limit,
        warnings=warnings,
        summary="document extracted from managed attachment",
    )


def handle_file_extract_document_image(inv, *, file_id: str = "", image_index: int = 1) -> dict[str, Any]:
    """Extract one embedded DOCX image into a managed temporary image record."""
    from storage.file_store import get_file_record, import_user_upload, resolve_file_path

    args = getattr(inv, "arguments", None) or {}
    file_id = file_id or str(args.get("file_id") or "")
    image_index = int(args.get("image_index", image_index) or 1)
    ws = getattr(inv, "workspace_id", None) or ""
    record = get_file_record(ws, file_id)
    if not record:
        return _fail("workspace.file", "file_not_found", file_id=file_id)
    if record.get("file_kind") != "docx":
        return _fail("workspace.file", "embedded_image_extraction_requires_docx", file_id=file_id)
    if image_index < 1 or image_index > 50:
        return _fail("workspace.file", "invalid_image_index", file_id=file_id)
    try:
        with zipfile.ZipFile(resolve_file_path(ws, file_id)) as document:
            names = sorted(
                (name for name in document.namelist() if name.startswith("word/media/") and not name.endswith("/")),
                key=lambda name: (int("".join(ch for ch in Path(name).stem if ch.isdigit()) or 0), name),
            )
            if image_index > len(names):
                return _fail("workspace.file", "embedded_image_not_found", file_id=file_id, image_count=len(names))
            name = names[image_index - 1]
            raw = document.read(name)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return _fail("workspace.file", "embedded_image_extract_failed", file_id=file_id, detail=str(exc)[:200])

    suffix = Path(name).suffix.lower().lstrip(".")
    kind = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(suffix)
    if not kind:
        return _fail("workspace.file", "unsupported_embedded_image_format", file_id=file_id, image_index=image_index)
    image_record = import_user_upload(
        ws, BytesIO(raw), f"{Path(str(record.get('original_name') or 'document')).stem}_image_{image_index}.{suffix}",
        logical_type="tmp", file_kind=kind, binary=True, source="document_image_extract",
        session_id=str(getattr(inv, "session_id", "") or ""), run_id=str(getattr(inv, "run_id", "") or ""),
    )
    return _ok(
        "workspace.file", file_id=file_id, image_index=image_index, image_count=len(names),
        vision_attachment={"file_id": image_record.file_id, "kind": "image"},
        summary="embedded document image extracted for visual analysis",
    )


def handle_file_references(inv, *, file_id: str = "") -> dict[str, Any]:
    """Query ReferenceIndex for a file."""
    from storage.reference_index import list_references_for_file

    ws = getattr(inv, "workspace_id", None) or ""
    refs = list_references_for_file(ws, file_id)
    return _ok("workspace.filestore", references=refs, count=len(refs), file_id=file_id)


def handle_file_write_agent_output(
    inv, *, content: str = "", logical_type: str = "artifact_output",
    file_kind: str = "text", title: str = "", ext: str = "txt",
) -> dict[str, Any]:
    """Write content through FileStore.write_agent_output."""
    from storage.file_store import write_agent_output

    if not content:
        return _fail("workspace.file", "content_required")

    ws = getattr(inv, "workspace_id", None) or ""
    run_id = getattr(inv, "run_id", "")
    rec = write_agent_output(
        workspace_id=ws, content=content, logical_type=logical_type,
        file_kind=file_kind, title=title or "agent_output", ext=ext,
        source="tool_runtime", run_id=run_id,
    )
    return _ok("workspace.file", file_id=rec.file_id, path=rec.path,
               size_bytes=rec.size_bytes, sha256=rec.sha256)


def handle_file_import_workspace_path(inv, *, filepath: str = "") -> dict[str, Any]:
    """Import a workspace-managed file into FileStore."""
    from storage.file_store import import_user_upload
    from storage.workspace_files import resolve_importable_workspace_path

    if not filepath:
        return _fail("workspace.filestore", "filepath_required")

    ws = getattr(inv, "workspace_id", None) or ""
    try:
        target = resolve_importable_workspace_path(ws, filepath)
    except ValueError as exc:
        if str(exc) == "path_not_allowed":
            return _fail("workspace.filestore", "path_not_allowed", filepath=filepath)
        return _fail("workspace.filestore", "path_not_in_workspace", filepath=filepath)

    if not target.exists():
        return _fail("workspace.filestore", "file_not_found", filepath=filepath)

    rec = import_user_upload(
        workspace_id=ws, file_source=str(target), original_name=target.name,
        source="file_import_workspace_path",
    )
    return _ok("workspace.filestore", file_id=rec.file_id, path=rec.path,
               size_bytes=rec.size_bytes, sha256=rec.sha256)
