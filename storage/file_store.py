# storage/file_store.py
"""Unified file storage layer.

All file creation in the workspace MUST go through this module.
Files are indexed in ``index/files.jsonl`` and stored under
``files/<category>/`` within the workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Union

from storage.paths import workspace_root, ensure_workspace_storage_dirs
from storage.schemas import FileRecord
from storage import index  # P2-B: locked, atomic index operations


# ── Helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gen_file_id() -> str:
    return f"file_{uuid.uuid4().hex[:16]}"


def _safe_name(name: str, max_len: int = 80) -> str:
    """Sanitize a filename for safe filesystem use while preserving CJK chars."""
    safe = re.sub(
        r"[^a-zA-Z0-9_.\-\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\U0002a700-\U0002ebef]",
        "_",
        name or "unnamed",
    )
    return safe[:max_len] or "unnamed"


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_LOGICAL_TYPE_TO_DIR = {
    "user_upload": "files/data",
    "chat_attachment": "files/data",
    "document_input": "files/data",
    "data_input": "files/data",
    "knowledge_normalized": "files/data",
    "artifact_output": "files/data",
    "report": "files/data",
    "message_large_content": "files/data",
    "tmp": "files/tmp",
}


def _dir_for_type(logical_type: str) -> str:
    return _LOGICAL_TYPE_TO_DIR.get(logical_type, "files/data")


# ── Public API ───────────────────────────────────────────────────────

def _resolve_workspace_relative_path(workspace_id: str, rel_path: str) -> Path:
    """Resolve a workspace-relative path and enforce containment."""
    if not rel_path:
        raise ValueError("empty file path")
    p = Path(str(rel_path))
    if p.is_absolute():
        raise ValueError("file path must be workspace-relative")
    ws = workspace_root(workspace_id).resolve()
    target = (ws / p).resolve()
    try:
        target.relative_to(ws)
    except ValueError as exc:
        raise ValueError(f"path_escape_denied: {rel_path}") from exc
    return target


def create_file_record(
    workspace_id: str,
    logical_type: str,
    file_kind: str,
    path: str,
    *,
    original_name: str = "",
    mime_type: str = "",
    binary: bool = False,
    size_bytes: int = 0,
    sha256: str = "",
    created_by: str = "system",
    session_id: str = "",
    run_id: str = "",
    source: str = "",
    sensitivity: str = "internal",
    metadata: dict[str, Any] | None = None,
    file_id: str = "",
) -> FileRecord:
    """Create and index a FileRecord (file must already exist on disk)."""
    # Validate path: must be workspace-relative, within workspace boundary, and an existing file
    target = _resolve_workspace_relative_path(workspace_id, path)
    if not target.exists():
        raise FileNotFoundError(f"file not found for FileRecord: {path}")
    if not target.is_file():
        raise ValueError(f"FileRecord path is not a file: {path}")

    # Auto-fill size and hash if not provided
    if not size_bytes:
        size_bytes = target.stat().st_size
    if not sha256:
        sha256 = _sha256_of_file(target)

    fid = file_id or _gen_file_id()
    rec = FileRecord(
        file_id=fid,
        workspace_id=workspace_id,
        logical_type=logical_type,
        file_kind=file_kind,
        path=path,
        original_name=original_name,
        mime_type=mime_type,
        binary=binary,
        size_bytes=size_bytes,
        sha256=sha256,
        created_at=_now_iso(),
        created_by=created_by,
        session_id=session_id,
        run_id=run_id,
        source=source,
        sensitivity=sensitivity,
        metadata=metadata or {},
    )
    index.append_file_record(workspace_id, rec)
    return rec


def import_user_upload(
    workspace_id: str,
    file_source: Union[str, Path, IO[bytes]],
    original_name: str,
    *,
    logical_type: str = "user_upload",
    file_kind: str = "text",
    binary: bool = False,
    source: str = "user_upload",
    session_id: str = "",
    run_id: str = "",
    sensitivity: str = "internal",
    metadata: dict[str, Any] | None = None,
) -> FileRecord:
    """Import an uploaded file into managed storage, preserving the original."""
    ensure_workspace_storage_dirs(workspace_id)
    ws = workspace_root(workspace_id)
    fid = _gen_file_id()
    safe = _safe_name(original_name)
    rel_dir = _dir_for_type(logical_type)
    target_dir = ws / rel_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{fid}__{safe}"
    rel_path = target.relative_to(ws).as_posix()

    # Write file
    if isinstance(file_source, (str, Path)):
        src = Path(file_source)
        shutil.copy2(str(src), str(target))
    else:
        # File-like object (stream)
        with open(target, "wb") as out:
            while True:
                chunk = file_source.read(65536)
                if not chunk:
                    break
                out.write(chunk)

    size = target.stat().st_size
    sha = _sha256_of_file(target)
    mime = _guess_mime(original_name, binary)

    # Policy enforcement: reject disallowed kinds or oversized files
    from storage.policy import MAX_UPLOAD_BYTES, ALLOWED_UPLOAD_KINDS
    if file_kind not in ALLOWED_UPLOAD_KINDS:
        try:
            target.unlink()
        except OSError:
            pass
        raise ValueError(f"unsupported_file_kind: {file_kind}")
    if size > MAX_UPLOAD_BYTES:
        try:
            target.unlink()
        except OSError:
            pass
        raise ValueError(f"file_too_large: {size} > {MAX_UPLOAD_BYTES}")

    return create_file_record(
        workspace_id=workspace_id,
        logical_type=logical_type,
        file_kind=file_kind,
        path=rel_path,
        original_name=original_name,
        mime_type=mime,
        binary=binary,
        size_bytes=size,
        sha256=sha,
        source=source,
        session_id=session_id,
        run_id=run_id,
        sensitivity=sensitivity,
        metadata=metadata,
        file_id=fid,
    )


def write_agent_output(
    workspace_id: str,
    content: Union[str, bytes],
    logical_type: str,
    file_kind: str,
    *,
    title: str = "",
    ext: str = "",
    source: str = "agent",
    run_id: str = "",
    session_id: str = "",
    sensitivity: str = "internal",
    metadata: dict[str, Any] | None = None,
) -> FileRecord:
    """Write agent-generated output to managed storage."""
    ensure_workspace_storage_dirs(workspace_id)
    ws = workspace_root(workspace_id)
    fid = _gen_file_id()
    safe_title = _safe_name(title or fid)
    if not ext:
        ext = _ext_for_kind(file_kind)
    rel_dir = _dir_for_type(logical_type)
    target_dir = ws / rel_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    # Write to tmp first, then rename (atomic-ish)
    tmp_dir = ws / "files" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"{fid}.tmp"
    target = target_dir / f"{fid}__{safe_title}.{ext}"
    rel_path = target.relative_to(ws).as_posix()

    is_binary = isinstance(content, bytes)
    if is_binary:
        tmp_file.write_bytes(content)
    else:
        tmp_file.write_text(content, encoding="utf-8")

    shutil.move(str(tmp_file), str(target))

    size = target.stat().st_size
    data = content if is_binary else content.encode("utf-8")
    sha = _sha256_of_bytes(data)

    return create_file_record(
        workspace_id=workspace_id,
        logical_type=logical_type,
        file_kind=file_kind,
        path=rel_path,
        original_name=f"{safe_title}.{ext}",
        mime_type=_guess_mime(f"{safe_title}.{ext}", is_binary),
        binary=is_binary,
        size_bytes=size,
        sha256=sha,
        source=source,
        run_id=run_id,
        session_id=session_id,
        sensitivity=sensitivity,
        metadata=metadata,
        file_id=fid,
    )


def write_knowledge_document(
    workspace_id: str,
    source_id: str,
    content: str,
    *,
    title: str,
    file_id: str = "",
) -> FileRecord:
    """Create or replace one canonical normalized knowledge document.

    Knowledge has one stable Markdown path per source. Source metadata remains
    in ContextStore; this managed file stores only the normalized body.
    """
    if not re.fullmatch(r"ksrc_[0-9a-f]{12}", str(source_id or "")):
        raise ValueError("invalid knowledge source_id")
    ensure_workspace_storage_dirs(workspace_id)
    ws = workspace_root(workspace_id)
    target = ws / "files" / "data" / f"{source_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = ws / "files" / "tmp" / f"{source_id}.{uuid.uuid4().hex[:8]}.tmp"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(str(content or ""), encoding="utf-8")
    os.replace(tmp, target)

    rel_path = target.relative_to(ws).as_posix()
    data = target.read_bytes()
    updates = {
        "logical_type": "knowledge_normalized",
        "file_kind": "markdown",
        "path": rel_path,
        "original_name": f"{source_id}.md",
        "mime_type": "text/markdown",
        "binary": False,
        "size_bytes": len(data),
        "sha256": _sha256_of_bytes(data),
        "source": "knowledge_import",
        "sensitivity": "internal",
        "lifecycle": "active",
        "metadata": {
            "source_id": source_id,
            "title": str(title or "")[:200],
            "storage_managed": True,
            "normalized_format": "markdown",
        },
    }
    if file_id and index.update_file_record(workspace_id, file_id, updates):
        rec = get_file_record(workspace_id, file_id)
        return FileRecord(**{k: v for k, v in rec.items() if k in FileRecord.__dataclass_fields__})
    return create_file_record(
        workspace_id=workspace_id,
        logical_type="knowledge_normalized",
        file_kind="markdown",
        path=rel_path,
        original_name=f"{source_id}.md",
        mime_type="text/markdown",
        binary=False,
        size_bytes=len(data),
        sha256=_sha256_of_bytes(data),
        source="knowledge_import",
        sensitivity="internal",
        metadata=updates["metadata"],
    )


def read_file_content(workspace_id: str, file_id: str) -> str:
    """Read text content of a managed file by file_id."""
    rec = get_file_record(workspace_id, file_id)
    if not rec:
        raise FileNotFoundError(f"No file record for {file_id}")
    if rec.get("binary"):
        raise ValueError(f"binary file cannot be read as text: {file_id}")
    path = resolve_file_path(workspace_id, file_id)
    return path.read_text(encoding="utf-8", errors="replace")


def read_workspace_text_file(workspace_id: str, filepath: str, *, max_bytes: int = 512_000) -> str:
    """Read a workspace-relative text file with containment and size checks."""
    target = _resolve_workspace_relative_path(workspace_id, filepath)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"file not found: {filepath}")
    data = target.read_bytes()
    if len(data) > max_bytes:
        raise ValueError("source_config_too_large")
    return data.decode("utf-8", errors="replace")


def resolve_file_path(workspace_id: str, file_id: str) -> Path:
    """Resolve a file_id to its absolute filesystem path."""
    rec = get_file_record(workspace_id, file_id)
    if not rec:
        raise FileNotFoundError(f"No file record for {file_id}")
    ws = workspace_root(workspace_id).resolve()
    resolved = (ws / rec["path"]).resolve()
    try:
        resolved.relative_to(ws)
    except ValueError as exc:
        raise ValueError(f"Path escape denied for {file_id}") from exc
    if not resolved.exists():
        raise FileNotFoundError(f"File not found on disk: {rec['path']}")
    if not resolved.is_file():
        raise ValueError(f"FileRecord path is not a file: {rec['path']}")
    return resolved


def get_file_record(workspace_id: str, file_id: str) -> dict | None:
    """Look up a FileRecord from the JSONL index."""
    records = index.read_file_records(workspace_id)
    for rec in records:
        if rec.get("file_id") == file_id:
            return rec
    return None


def list_files(workspace_id: str, *, logical_type: str = "", lifecycle: str = "active") -> list[dict]:
    """List file records, optionally filtered by logical_type and lifecycle."""
    records = index.read_file_records(workspace_id)
    results = []
    for rec in records:
        if lifecycle and rec.get("lifecycle") != lifecycle:
            continue
        if logical_type and rec.get("logical_type") != logical_type:
            continue
        results.append(rec)
    return results


def soft_delete_file(workspace_id: str, file_id: str) -> bool:
    """Mark a file as soft-deleted (does not remove from disk)."""
    rec = get_file_record(workspace_id, file_id)
    if not rec:
        return False
    if rec.get("lifecycle") == "soft_deleted":
        return True
    if rec.get("lifecycle") == "purged":
        return False
    index.update_file_record(workspace_id, file_id, {
        "lifecycle": "soft_deleted",
        "metadata": {**rec.get("metadata", {}), "deleted_at": _now_iso()},
    })
    return True


def purge_file(workspace_id: str, file_id: str) -> bool:
    """Remove one managed payload and mark its index record purged."""
    rec = get_file_record(workspace_id, file_id)
    if not rec:
        return False
    if rec.get("lifecycle") == "purged":
        return True
    try:
        path = _resolve_workspace_relative_path(workspace_id, rec["path"])
        if path.exists():
            path.unlink()
    except (OSError, ValueError):
        return False
    return index.update_file_record(workspace_id, file_id, {
        "lifecycle": "purged",
        "metadata": {**rec.get("metadata", {}), "purged_at": _now_iso()},
    })


def delete_file_permanently(workspace_id: str, file_id: str) -> bool:
    """Delete the managed payload and remove its metadata from the file index."""
    rec = get_file_record(workspace_id, file_id)
    if not rec:
        return False
    try:
        path = _resolve_workspace_relative_path(workspace_id, rec["path"])
        if path.exists():
            path.unlink()
    except (OSError, ValueError):
        return False
    return index.delete_file_record(workspace_id, file_id)


# ── Internal helpers ─────────────────────────────────────────────────

def _guess_mime(name: str, binary: bool = False) -> str:
    ext = Path(name).suffix.lower()
    mime_map = {
        ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json",
        ".yaml": "text/yaml", ".yml": "text/yaml", ".xml": "text/xml",
        ".csv": "text/csv", ".html": "text/html", ".log": "text/plain",
        ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return mime_map.get(ext, "application/octet-stream" if binary else "text/plain")


def _ext_for_kind(kind: str) -> str:
    ext_map = {
        "text": "txt", "config": "txt", "markdown": "md", "json": "json",
        "yaml": "yaml", "xml": "xml", "csv": "csv", "html": "html",
        "log": "log", "diff": "diff", "script": "sh",
    }
    return ext_map.get(kind, "txt")
