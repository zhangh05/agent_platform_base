# agent/modules/knowledge/store.py
"""Knowledge Store — delegates to unified ContextStore.

All source records are stored as item_type="knowledge_source" in ContextStore
(items.jsonl).
"""

from __future__ import annotations

import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from core.context.context_store import get_context_store
from core.context.unified_retriever import get_retriever
from agent.runtime.utils import now_iso


SOURCE_ID_PREFIX = "ksrc_"
_LOG = logging.getLogger("knowledge.store")


def _now_iso() -> str:
    return now_iso()


def _generate_source_id() -> str:
    return f"{SOURCE_ID_PREFIX}{uuid.uuid4().hex[:12]}"


def _sanitize_source_label(source: str) -> str:
    """Strip local paths from source labels."""
    source = str(source or "").strip()
    if re.match(r"^[A-Za-z]:\\|^/", source):
        return re.sub(r"^.*[/\\]", "", source)
    return source[:200]


def _public_view(rec: dict, include_content: bool = False) -> dict:
    """Project a ContextStore item to the public source view."""
    meta = rec.get("metadata", {}) or {}
    out = {
        "source_id": rec.get("source_id", rec.get("item_id", "")),
        "title": rec.get("title", ""),
        "source": _sanitize_source_label(meta.get("origin_source", rec.get("source", ""))),
        "enabled": not rec.get("disabled", False),
        "deleted": rec.get("deleted", False),
        "created_at": rec.get("created_at", ""),
        "updated_at": meta.get("updated_at", rec.get("created_at", "")),
        "summary": rec.get("summary", ""),
        "scope": rec.get("scope", meta.get("scope", "workspace")),
        "source_type": meta.get("source_type", "project_doc"),
        "format": "markdown",
        "language": meta.get("language", ""),
        "tags": rec.get("tags", meta.get("tags", [])),
        "metadata": {
            k: v for k, v in meta.items()
            if k not in ("content", "normalized_markdown", "origin_source")
        },
    }
    if include_content:
        out["content"] = rec.get("content", "")
        out["normalized_markdown"] = meta.get("normalized_markdown", "")
    return out


# ─── Public API ───

def import_document(
    workspace_id: str,
    title: str,
    content: str,
    source: str = "",
    metadata: dict = None,
) -> dict:
    """Import a document as a knowledge source into ContextStore."""
    content = str(content or "")
    if not content.strip():
        return {"ok": False, "errors": ["empty_document"]}
    preview = content[:1000]  # Full text lives in managed workspace storage.

    source_id = _generate_source_id()
    meta = dict(metadata or {})
    meta["origin_source"] = _sanitize_source_label(source)
    meta["updated_at"] = _now_iso()
    meta["content_length"] = len(content)

    # Source records stay lightweight, while the complete normalized text is
    # kept in managed workspace storage. This preserves read/reindex semantics
    # without duplicating multi-megabyte documents in ContextStore JSONL.
    try:
        from storage.file_store import write_knowledge_document
        normalized = write_knowledge_document(
            workspace_id=workspace_id,
            source_id=source_id,
            content=content,
            title=title.strip()[:200],
        )
        normalized_file_id = normalized.file_id
        meta["normalized_file_id"] = normalized_file_id
        meta["storage_managed"] = True
    except Exception as exc:
        return {
            "ok": False,
            "errors": ["normalized_content_store_failed"],
            "summary": str(exc)[:200],
        }

    item = {
        "item_id": source_id,
        "item_type": "knowledge_source",
        "source": "knowledge_import",
        "source_id": source_id,
        "title": title.strip()[:200],
        "summary": preview,
        "content": preview,  # Store preview only; full content in chunks
        "scope": meta.pop("scope", "workspace"),
        "sensitivity": "internal",
        "tags": meta.pop("tags", []),
        "metadata": meta,
    }

    store = get_context_store(workspace_id)
    try:
        store.put(item)
        # Direct text imports receive basic search chunks. File-ingestion
        # callers may replace these with richer structural chunks afterwards.
        _create_basic_chunks(workspace_id, source_id, title.strip()[:200], content, meta)
    except Exception as exc:
        try:
            delete_source(workspace_id, source_id)
            from storage.file_store import soft_delete_file
            soft_delete_file(workspace_id, str(meta.get("normalized_file_id") or ""))
        except Exception:
            _LOG.warning("failed to roll back knowledge source %s", source_id, exc_info=True)
        return {
            "ok": False,
            "errors": ["knowledge_chunk_store_failed"],
            "summary": str(exc)[:200],
        }

    return {
        "ok": True,
        "source_id": source_id,
        "title": item["title"],
        "normalized_file_id": meta["normalized_file_id"],
        "summary": f"Imported: {item['title']} ({len(content)} chars)",
    }


def _create_basic_chunks(workspace_id: str, source_id: str, title: str, content: str, meta: dict = None):
    """Create simple chunks from content for BM25 searchability."""
    if not content or not content.strip():
        return
    meta = meta or {}
    store = get_context_store(workspace_id)
    chunk_size = 800
    overlap = 100
    text = content.strip()
    chunks = []
    i = 0
    idx = 0
    while i < len(text):
        end = min(i + chunk_size, len(text))
        chunk_text = text[i:end]
        chunk_id = f"kch_{source_id[5:]}_{idx:04d}" if source_id.startswith("ksrc_") else f"kch_{uuid.uuid4().hex[:8]}_{idx:04d}"
        chunks.append({
            "item_id": f"kc_{chunk_id}",
            "item_type": "knowledge_chunk",
            "source": "knowledge_import",
            "source_id": source_id,
            "title": title,
            "content": chunk_text,
            "chunk_id": chunk_id,
            "chunk_type": "child",
            "chunk_index": idx,
            "scope": "workspace",
            "metadata": {
                "source_type": meta.get("source_type", "document"),
                "artifact_id": meta.get("artifact_id", ""),
            },
        })
        idx += 1
        i = end - overlap if end < len(text) else end
    if chunks:
        store.put_many(chunks)


def list_sources(
    workspace_id: str,
    include_disabled: bool = False,
    include_deleted: bool = False,
    query: str = "",
    scope: str = "",
) -> dict:
    """List knowledge sources, optionally filtered by title/source substring."""
    store = get_context_store(workspace_id)
    items = store.list_items(item_type="knowledge_source", limit=999)

    query_str = (query or "").strip().lower()
    sources = []
    for item in items:
        if item.get("deleted") and not include_deleted:
            continue
        if item.get("disabled") and not include_disabled:
            continue
        if scope and item.get("scope", (item.get("metadata") or {}).get("scope", "workspace")) != scope:
            continue
        if query_str:
            title = (item.get("title") or "").lower()
            meta = item.get("metadata", {}) or {}
            origin = (meta.get("origin_source") or "").lower()
            if query_str not in title and query_str not in origin:
                continue
        sources.append(_public_view(item))

    return {
        "ok": True,
        "sources": sources,
        "total": len(sources),
        "query": query_str,
    }


def read_source(workspace_id: str, source_id: str) -> Optional[dict]:
    """Read a single source with content."""
    store = get_context_store(workspace_id)
    item = store.get(source_id)
    if not item or item.get("item_type") != "knowledge_source":
        return None
    out = _public_view(item, include_content=True)
    normalized_file_id = str((item.get("metadata") or {}).get("normalized_file_id") or "")
    if normalized_file_id:
        try:
            from storage.file_store import read_file_content
            full_content = read_file_content(workspace_id, normalized_file_id)
            out["content"] = full_content
            out["normalized_markdown"] = full_content
        except (FileNotFoundError, OSError, ValueError):
            out.setdefault("warnings", []).append("normalized_content_unavailable")
    return out


def disable_source(
    workspace_id: str, source_id: str, disabled: bool = True
) -> Optional[dict]:
    """Enable/disable a source."""
    store = get_context_store(workspace_id)
    item = store.get(source_id)
    if not item or item.get("item_type") != "knowledge_source":
        return None

    # Write updated version (append-only JSONL, last wins)
    item["disabled"] = disabled
    meta = item.get("metadata", {})
    meta["updated_at"] = _now_iso()
    item["metadata"] = meta
    store.put(item)

    chunks = store.list_items(
        item_type="knowledge_chunk",
        source_id=source_id,
        include_deleted=False,
        limit=999_999,
    )
    if chunks:
        for chunk in chunks:
            chunk["disabled"] = disabled
        store.put_many(chunks)

    return _public_view(item)


def delete_source(workspace_id: str, source_id: str) -> bool:
    """Physically delete a source and its chunks — purge from ContextStore."""
    store = get_context_store(workspace_id)
    source = store.get(source_id)
    if not source or source.get("item_type") != "knowledge_source":
        return False
    source_meta = (source or {}).get("metadata", {})
    file_ids = {str(source_meta.get("normalized_file_id") or "")}
    # Collect source + all associated chunk IDs
    ids_to_purge = {source_id}
    chunks = store.list_items(item_type="knowledge_chunk", source_id=source_id, include_deleted=True, limit=999_999)
    for chunk in chunks:
        ids_to_purge.add(chunk["item_id"])
    store.purge(ids_to_purge)
    for file_id in file_ids - {""}:
        try:
            from storage.file_store import purge_file
            purge_file(workspace_id, file_id)
        except Exception:
            _LOG.warning("failed to retire knowledge file for %s", source_id, exc_info=True)
    return True


def rename_source(workspace_id: str, source_id: str, title: str) -> Optional[dict]:
    """Rename a source and all chunk projections."""
    store = get_context_store(workspace_id)
    item = store.get(source_id)
    if not item or item.get("item_type") != "knowledge_source":
        return None

    new_title = title.strip()[:200]
    item["title"] = new_title
    meta = item.get("metadata", {})
    meta["updated_at"] = _now_iso()
    item["metadata"] = meta
    store.put(item)

    # Update title in all associated chunks (search results read from chunks)
    chunks = store.list_items(
        item_type="knowledge_chunk",
        source_id=source_id,
        include_deleted=False,
        limit=999_999,
    )
    if chunks:
        for chunk in chunks:
            chunk["title"] = new_title
        store.put_many(chunks)

    return _public_view(item)


def query(
    workspace_id: str,
    query: str,
    top_k: int = 5,
    filters: dict = None,
) -> dict:
    """Query knowledge via UnifiedRetriever."""
    if not query or not query.strip():
        return {"ok": True, "hits": [], "total": 0}

    retriever = get_retriever(workspace_id)
    source_type = (filters or {}).get("source_type")

    # Use unified retriever for both knowledge and memory
    if source_type == "memory":
        hits = retriever.search_memory(query, top_k=top_k)
    else:
        hits = retriever.search_knowledge(query, top_k=top_k)

    # Format hits
    formatted = []
    for h in hits:
        formatted.append({
            "chunk_id": h.get("chunk_id", h.get("item_id", "")),
            "source_id": h.get("source_id", ""),
            "title": h.get("title", ""),
            "chapter": h.get("chapter", ""),
            "section": h.get("section", ""),
            "content": h.get("content", ""),
            "snippet": str(h.get("content", ""))[:300],
            "score": h.get("_score", 0),
            "scope": h.get("scope", ""),
            "metadata": h.get("metadata", {}),
        })

    return {
        "ok": True,
        "hits": formatted,
        "total": len(formatted),
        "metadata": {"retrieval_backend": "unified_bm25"},
    }


def store_stats(workspace_id: str) -> dict:
    """Return store statistics."""
    store = get_context_store(workspace_id)
    return {
        "workspace_id": workspace_id,
        "source_count": store.count(item_type="knowledge_source"),
        "chunk_count": store.count(item_type="knowledge_chunk"),
        "memory_count": store.count(item_type="memory_hit"),
        "total_items": store.count(),
        "store_version": "3.1.0",
    }
