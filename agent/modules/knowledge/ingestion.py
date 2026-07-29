# agent/modules/knowledge/ingestion.py
"""Document ingestion (v1.0.1).

Pipeline:
  raw file (md/txt/html/docx/pdf)
    -> parse_document()  (parsers/)
    -> NormalizedDocument
    -> chunk_document()  (chunking.py)
    -> (parents, children)
    -> save to v1.0 KnowledgeStore (as a single source record
       containing the full normalized_markdown, plus metadata
       describing chunk counts) AND
       save parents + children to the chunk store (index.py)

We deliberately keep the v1.0 source store as the single source of
truth for *what* is in the library (title, author, edition, scope,
format, language) and the v1.0.1 chunk store as the source of truth
for *how* it is chunked. The two are cross-referenced via source_id.

Strict contract:
  - Never fabricate author / edition / page numbers / chapter
    numbers. The parsers only surface what the format itself
    declares.
  - Scanned PDFs return ok=False with error="unsupported_ocr".
  - The full normalized_markdown is stored verbatim in the source
    record; children do NOT mix in any "retrieval prefix".

v1.0.1.1 — Knowledge Ingestion Security:

  import_file only accepts paths inside an allowlist:
      {ws_root}/{workspace_id}/files/
      {ws_root}/{workspace_id}/inbox/
  All other paths are rejected with `path_not_allowed`.

  Hardening enforced in `_validate_import_path`:
    - Path must resolve inside one of the allowlisted roots
      (resolve() catches `..` and symlink escapes).
    - Reject symlinks that escape the allowlisted roots
      (resolve() then check is_relative_to).
    - File must exist and be a regular file.
    - Default max file size = 50 MB.
    - PDF max page count = 2000 (configurable).
    - DOCX / ZIP / EPUB-style archives are inspected for
      archive-bomb patterns: total uncompressed size <= 200 MB,
      per-entry ratio <= 100x, total entries <= 1000.
"""

from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Union

from agent.modules.knowledge import parsers as _parsers
from agent.modules.knowledge import chunking as _chunking
from agent.modules.knowledge import index as _index
from agent.modules.knowledge import store as _store
from agent.modules.knowledge.schemas import (
    KnowledgeChunk, NormalizedDocument, SOURCE_TYPES, SCOPES,
)


# ── v1.0.1.1 Security Config ──

# Hard caps; can be overridden by env vars (so tests can lower them).
DEFAULT_MAX_FILE_BYTES = int(
    os.environ.get("KNOWLEDGE_MAX_FILE_BYTES", str(200 * 1024 * 1024))  # 200 MB
)
DEFAULT_MAX_PDF_PAGES = int(
    os.environ.get("KNOWLEDGE_MAX_PDF_PAGES", "2000")
)
DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES = int(
    os.environ.get("KNOWLEDGE_MAX_ZIP_UNCOMPRESSED_BYTES",
                   str(200 * 1024 * 1024))  # 200 MB
)
DEFAULT_MAX_ZIP_RATIO = int(
    os.environ.get("KNOWLEDGE_MAX_ZIP_RATIO", "100")
)
DEFAULT_MAX_ZIP_ENTRIES = int(
    os.environ.get("KNOWLEDGE_MAX_ZIP_ENTRIES", "1000")
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Path security ──

def _allowed_import_roots(workspace_id: str) -> List[Path]:
    """Allowlist of directories import_file will accept from.

    Only these may be ingested:
      - workspace/{ws_id}/files/  (explicit user upload)
      - workspace/{ws_id}/inbox/    (staging area)
    """
    from storage.workspace_files import allowed_import_roots
    return allowed_import_roots(workspace_id)


def _validate_import_path(workspace_id: str, path: Union[str, Path]) -> dict:
    """Validate a file path for import_file.

    Returns ok=True with `resolved_path` + `size_bytes` on success.
    Returns ok=False with a standard `errors=[...]` list otherwise.
    """
    if not workspace_id:
        return {"ok": False, "errors": ["path_not_allowed"]}
    p = Path(str(path or ""))
    if not p.is_absolute():
        return {"ok": False, "errors": ["path_not_allowed"]}
    # Pre-check: any ".." components? (defensive; resolve() also
    # catches this, but giving a clean error here is nicer.)
    raw = str(path)
    if ".." in Path(raw).parts:
        return {"ok": False, "errors": ["path_not_allowed"]}
    try:
        # Resolve the path (follows symlinks).
        resolved = p.resolve(strict=False)
    except (OSError, RuntimeError):
        return {"ok": False, "errors": ["file_not_found"]}
    # Check the resolved path is inside an allowlisted root.
    allow = _allowed_import_roots(workspace_id)
    inside = False
    for root in allow:
        try:
            root_resolved = root.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        try:
            resolved.relative_to(root_resolved)
            inside = True
            break
        except ValueError:
            continue
    if not inside:
        return {"ok": False, "errors": ["path_not_allowed"]}
    # Existence + regular file.
    if not resolved.exists() or not resolved.is_file():
        return {"ok": False, "errors": ["file_not_found"]}
    # File size cap.
    try:
        size = resolved.stat().st_size
    except OSError:
        return {"ok": False, "errors": ["file_not_found"]}
    if size > DEFAULT_MAX_FILE_BYTES:
        limit_mb = DEFAULT_MAX_FILE_BYTES // (1024 * 1024)
        return {"ok": False, "errors": [f"file_too_large: {size // (1024*1024)}MB exceeds {limit_mb}MB limit"]}
    return {
        "ok": True,
        "resolved_path": resolved,
        "size_bytes": size,
    }


def _check_archive_bomb(path: Path) -> dict:
    """Inspect a ZIP-style archive (DOCX, EPUB, ...) for archive-bomb
    patterns.

    Returns ok=True if safe; ok=False with a standard error otherwise.
    """
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            infos = zf.infolist()
            if len(infos) > DEFAULT_MAX_ZIP_ENTRIES:
                return {"ok": False, "errors": ["archive_too_large"]}
            total_uncompressed = 0
            for info in infos:
                total_uncompressed += info.file_size
                if total_uncompressed > DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES:
                    return {"ok": False, "errors": ["archive_too_large"]}
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > DEFAULT_MAX_ZIP_RATIO:
                        return {"ok": False, "errors": ["archive_too_large"]}
    except (zipfile.BadZipFile, OSError):
        return {"ok": False, "errors": ["invalid_file"]}
    return {"ok": True}


def _check_pdf_page_count(path: Path) -> dict:
    """Reject pathological PDFs by page count."""
    try:
        import pdfplumber  # type: ignore
        import io
        with pdfplumber.open(io.BytesIO(path.read_bytes())) as pdf:
            n = len(pdf.pages)
            if n > DEFAULT_MAX_PDF_PAGES:
                return {"ok": False, "errors": ["file_too_large"]}
    except ImportError:
        return {"ok": True}  # parser will return its own error
    except Exception:
        return {"ok": True}  # parser will detect invalid file
    return {"ok": True}


# ── Ingestion ──

def import_file(
    workspace_id: str,
    source: Union[str, bytes, "Path"],
    *,
    title: str = "",
    author: str = "",
    edition: str = "",
    source_type: str = "project_doc",
    scope: str = "workspace",
    language: str = "zh",
    tags: Optional[List[str]] = None,
    metadata: Optional[dict] = None,
    file_id: str = "",
) -> dict:
    """Top-level ingestion: file -> source + chunks.

    v1.0.1.1: when `source` is a path, it MUST be inside the
    workspace allowlist (files / inbox). Bytes sources are still
    accepted (they are pre-validated by the caller / API layer).

    Returns:
      {
        ok, summary, source_id,
        source_type, format, scope, language,
        chunk_count, parent_count,
        warnings, errors,
      }
    """
    if not workspace_id:
        return {"ok": False, "summary": "workspace_id is required",
                "errors": ["missing_workspace_id"]}
    if source_type not in SOURCE_TYPES:
        return {"ok": False,
                "summary": f"invalid source_type: {source_type}",
                "errors": ["invalid_source_type"]}
    if scope not in SCOPES:
        return {"ok": False,
                "summary": f"invalid scope: {scope}",
                "errors": ["invalid_scope"]}

    # v1.0.1.1 — Path security check (only when source is a path).
    # If file_id is provided, resolve via FileStore and bypass allowlist.
    source_file_id = (file_id or "").strip()
    if source_file_id:
        try:
            from storage.file_store import resolve_file_path
            resolved_fs = resolve_file_path(workspace_id, source_file_id)
            source = resolved_fs
        except Exception as exc:
            return {
                "ok": False,
                "summary": f"file_id 无效: {str(exc)[:200]}",
                "errors": ["invalid_file_id"],
            }

    is_path_like = isinstance(source, (str, Path)) and not (
        isinstance(source, str) and (
            source.startswith("ksrc_") or len(source) < 4096
        ) and (
            # Heuristic: treat a str source as a path only if it
            # looks like one (has a path separator OR ends in a
            # known extension). Otherwise, treat as raw content.
            "/" in str(source) or "\\" in str(source)
            or str(source).lower().endswith((
                ".md", ".markdown", ".txt", ".html", ".htm",
                ".docx", ".pdf",
            ))
        )
    )
    raw_path = None
    if isinstance(source, (str, Path)) and not isinstance(source, bytes):
        raw_path = str(source)
        # Skip validation only for content-typed str (we want to
        # reject paths that look like content). The above heuristic
        # is too coarse; we will validate ANY str/Path that came
        # through the LLM-callable tool path. Test code that passes
        # raw bytes is unaffected.
    if raw_path is not None:
        check = _validate_import_path(workspace_id, raw_path)
        if not check.get("ok"):
            return {
                "ok": False,
                "summary": (
                    "import_file 拒绝: 路径不在白名单 (files/ 或 "
                    "inbox/)，或文件不可访问。"
                ),
                "errors": check.get("errors", ["path_not_allowed"]),
            }
        # Archive bomb / page count checks per format.
        fp = check["resolved_path"]
        if str(fp).lower().endswith(".docx"):
            ac = _check_archive_bomb(fp)
            if not ac.get("ok"):
                return {
                    "ok": False,
                    "summary": (
                        "DOCX / ZIP 文件超过安全限制 (解压大小 / "
                        "压缩比 / 入口数)。可能是 archive bomb。"
                    ),
                    "errors": ac.get("errors", ["archive_too_large"]),
                }
        if str(fp).lower().endswith(".pdf"):
            pc = _check_pdf_page_count(fp)
            if not pc.get("ok"):
                return {
                    "ok": False,
                    "summary": (
                        f"PDF 页数过多 (>{DEFAULT_MAX_PDF_PAGES} 页)，"
                        "已拒绝。请拆分后重试。"
                    ),
                    "errors": pc.get("errors", ["file_too_large"]),
                }

    # 1. Parse
    try:
        doc = _parsers.parse_document(
            source,
            title=title, author=author, source_type=source_type,
            scope=scope, language=language, metadata=metadata,
        )
    except _parsers.UnsupportedFormatError as e:
        return {"ok": False, "summary": f"unsupported format: {e}",
                "errors": ["unsupported_format"]}
    except Exception as e:
        return {"ok": False, "summary": f"parser error: {e!r}",
                "errors": ["parser_failed"]}

    # 2. Scanned PDF? -> return ok=False
    if "unsupported_ocr" in (doc.warnings or []):
        return {
            "ok": False,
            "summary": ("扫描型 PDF 不支持 OCR 解析。请提供文本型 PDF "
                        "或转换为可解析的格式后重试。"),
            "format": doc.format,
            "errors": ["unsupported_ocr"],
            "warnings": doc.warnings,
        }

    if not doc.normalized_markdown.strip():
        return {
            "ok": False,
            "summary": f"document produced empty normalized_markdown",
            "format": doc.format,
            "errors": ["empty_document"],
            "warnings": doc.warnings,
        }

    # 3. Save to v1.0 source store
    full_title = (title or doc.title or "").strip()[:500] or "untitled"
    source_meta = dict(doc.metadata or {})
    source_meta.update({
        "source_type": source_type,
        "scope": scope,
        "language": language,
        "format": doc.format,
        "author": author or doc.author,
        "edition": edition,
        "tags": list(tags or []),
        "normalized_title": doc.title,
        "warnings": list(doc.warnings or []),
    })
    if source_file_id:
        source_meta["source_file_id"] = source_file_id
    saved = _store.import_document(
        workspace_id=workspace_id,
        title=full_title,
        content=doc.normalized_markdown,
        source=f"{source_type}/{doc.format}",
        metadata=source_meta,
    )
    if not saved.get("ok"):
        return {
            "ok": False,
            "summary": saved.get("summary", "import_document failed"),
            "errors": saved.get("errors", ["store_failed"]),
        }
    source_id = saved.get("source_id") or (saved.get("source") or {}).get("source_id", "")

    # 4. Chunk
    doc.source_id = source_id
    parents, children = _chunking.chunk_document(doc)

    # 5. Inject source-level metadata into each chunk's metadata.
    base_meta = {
        "scope": scope,
        "source_type": source_type,
        "source_title": full_title,
        "author": author or doc.author,
        "edition": edition,
        "language": language,
        "format": doc.format,
        "tags": list(tags or []),
    }
    if source_file_id:
        base_meta["source_file_id"] = source_file_id
    for key in ("hidden", "origin", "memory_id", "memory_type",
                "memory_scope", "memory_confidence", "memory_source"):
        if key in source_meta:
            base_meta[key] = source_meta.get(key)
    for c in parents:
        c.metadata.update(base_meta)
    for c in children:
        c.metadata.update(base_meta)
    # Re-build index_text for children with source_title (so BM25
    # scores against the title boost).
    for c in children:
        c.index_text = (
            f"{full_title} | {c.chapter} | {c.section} | "
            f"{' '.join(tags or [])} | {c.content}"
        )
    for c in parents:
        c.index_text = (
            f"{full_title} | {c.chapter} | {c.section} | "
            f"{' '.join(tags or [])} | {c.content}"
        )

    # 6. Write normalized markdown as FileRecord BEFORE chunk persistence
    normalized_file_id = str(saved.get("normalized_file_id") or "")

    # 7. Add file refs to chunk metadata before persistence
    if normalized_file_id:
        base_meta["normalized_file_id"] = normalized_file_id
    base_meta["storage_managed"] = bool(source_file_id or normalized_file_id)
    for c in parents:
        c.metadata.update(base_meta)
    for c in children:
        c.metadata.update(base_meta)
    # Re-build index_text for children with source_title (so BM25
    # scores against the title boost).
    for c in children:
        c.index_text = (
            f"{full_title} | {c.chapter} | {c.section} | "
            f"{' '.join(tags or [])} | {c.content}"
        )
    for c in parents:
        c.index_text = (
            f"{full_title} | {c.chapter} | {c.section} | "
            f"{' '.join(tags or [])} | {c.content}"
        )

    # 8. Save chunks AFTER metadata is complete
    n = _index.replace_chunks(workspace_id, source_id, parents + children)

    # ReferenceIndex: link source/normalized files to knowledge source
    try:
        from storage.reference_index import add_reference
        if source_file_id:
            add_reference(workspace_id, source_file_id, "knowledge_source", source_id, "source")
        if normalized_file_id:
            add_reference(workspace_id, normalized_file_id, "knowledge_source", source_id, "normalized")
    except Exception:
        pass

    result: dict[str, Any] = {
        "ok": True,
        "summary": (
            f"Imported {full_title} ({doc.format}) as {source_id} "
            f"with {len(parents)} parent + {len(children)} child chunks."
        ),
        "source_id": source_id,
        "title": full_title,
        "source_type": source_type,
        "format": doc.format,
        "scope": scope,
        "language": language,
        "parent_count": len(parents),
        "chunk_count": len(children),
        "warnings": list(doc.warnings or []),
        "errors": [],
    }
    if source_file_id:
        result["source_file_id"] = source_file_id
    if normalized_file_id:
        result["normalized_file_id"] = normalized_file_id
    return result


def reindex_source(workspace_id: str, source_id: str) -> dict:
    """Re-parse the source's normalized_markdown and rebuild chunks.

    Used after the source is updated in the v1.0 store, or when the
    chunker parameters have changed.

    v1.0.1.1: still callable by backend (not LLM-facing). Reads
    the stored normalized_markdown from the v1.0 source store.
    """
    if not workspace_id or not source_id:
        return {"ok": False, "summary": "workspace_id and source_id are required",
                "errors": ["missing_inputs"]}
    rec = _store.read_source(workspace_id, source_id)
    if rec is None:
        return {"ok": False,
                "summary": f"source not found: {source_id}",
                "errors": ["source_not_found"]}
    src = rec
    full_markdown = rec.get("content", "") or ""
    meta = src.get("metadata", {}) or {}
    doc = NormalizedDocument(
        source_id=source_id,
        title=src.get("title", ""),
        author=meta.get("author", ""),
        edition=meta.get("edition", ""),
        source_type=meta.get("source_type", "project_doc"),
        scope=meta.get("scope", "workspace"),
        language=meta.get("language", "zh"),
        format=meta.get("format", "md"),
        normalized_markdown=full_markdown,
        metadata=meta,
        warnings=list(meta.get("warnings") or []),
    )
    parents, children = _chunking.chunk_document(doc)
    base_meta = {
        "scope": doc.scope,
        "source_type": doc.source_type,
        "source_title": doc.title,
        "author": doc.author,
        "edition": doc.edition,
        "language": doc.language,
        "format": doc.format,
        "tags": list(meta.get("tags") or []),
    }
    for c in parents:
        c.metadata.update(base_meta)
    for c in children:
        c.metadata.update(base_meta)
    n = _index.replace_chunks(workspace_id, source_id, parents + children)
    return {
        "ok": True,
        "summary": f"Reindexed {source_id}: {len(parents)} parents, {len(children)} children",
        "source_id": source_id,
        "parent_count": len(parents),
        "chunk_count": len(children),
        "errors": [], "warnings": [],
    }
