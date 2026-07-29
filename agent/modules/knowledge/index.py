# agent/modules/knowledge/index.py
"""Knowledge index — delegates to ContextStore + UnifiedRetriever.

Chunk storage uses ContextStore (item_type="knowledge_chunk").
Search uses UnifiedRetriever (single BM25 engine).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional, Tuple

from agent.modules.knowledge.schemas import KnowledgeChunk
from core.context.context_store import get_context_store
from core.context.unified_retriever import get_retriever


# ─── Chunk persistence ───

def save_chunks(workspace_id: str, chunks: List[KnowledgeChunk]) -> int:
    """Save chunks to ContextStore. Returns count saved."""
    if not chunks:
        return 0

    store = get_context_store(workspace_id)
    items = []
    for chunk in chunks:
        d = chunk.to_dict() if hasattr(chunk, "to_dict") else dict(chunk)
        item = _chunk_to_item(d)
        items.append(item)

    store.put_many(items)
    return len(items)


def replace_chunks(
    workspace_id: str,
    source_id: str,
    new_chunks: List[KnowledgeChunk],
) -> dict:
    """Replace all chunks for a source_id with new_chunks."""
    store = get_context_store(workspace_id)

    # Delete old chunks for this source
    old = store.list_items(item_type="knowledge_chunk", source_id=source_id, limit=9999)
    for item in old:
        store.delete(item["item_id"])

    # Save new chunks
    count = save_chunks(workspace_id, new_chunks)

    return {
        "ok": True,
        "source_id": source_id,
        "replaced": len(old),
        "new_count": count,
    }


def load_all_chunks(workspace_id: str) -> List[KnowledgeChunk]:
    """Load all enabled chunks as KnowledgeChunk objects."""
    store = get_context_store(workspace_id)
    items = store.list_items(item_type="knowledge_chunk", limit=99999)
    return [_item_to_chunk(it) for it in items if it.get("disabled") is not True]


def list_chunks(
    workspace_id: str,
    source_id: str = "",
    chunk_type: str = "",
    enabled: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List chunks with optional filters."""
    store = get_context_store(workspace_id)
    items = store.list_items(
        item_type="knowledge_chunk",
        source_id=source_id if source_id else None,
        limit=limit + offset,
    )

    if enabled:
        items = [it for it in items if it.get("disabled") is not True]

    # Apply chunk_type filter
    if chunk_type:
        items = [it for it in items if it.get("chunk_type") == chunk_type]

    # Pagination
    page = items[offset:offset + limit]

    return {
        "ok": True,
        "chunks": [_item_to_dict(it) for it in page],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }


def get_chunk(workspace_id: str, chunk_id: str) -> Optional[KnowledgeChunk]:
    """Get a single chunk by ID."""
    store = get_context_store(workspace_id)

    # Try direct lookup
    item = store.get(chunk_id)
    if item and item.get("item_type") == "knowledge_chunk":
        return _item_to_chunk(item)

    # Try with kc_ prefix
    item = store.get(f"kc_{chunk_id}")
    if item and item.get("item_type") == "knowledge_chunk":
        return _item_to_chunk(item)

    return None


# ─── Search ───

def search_chunks(
    workspace_id: str,
    query: str,
    top_k: int = 5,
    source_type: str = "",
    source_id: str = "",
    scope: str = "",
    tags: list = None,
    chapter: str = "",
    min_score: float = 0.1,
    **kwargs,
) -> dict:
    """Search chunks via UnifiedRetriever."""
    if not query or not query.strip():
        return {"ok": True, "hits": [], "total": 0}

    retriever = get_retriever(workspace_id)

    # Determine item_type filter
    if source_type == "memory":
        hits = retriever.search_memory(query, top_k=top_k * 2, min_score=min_score)
    else:
        hits = retriever.search_knowledge(query, top_k=top_k * 2, min_score=min_score)

    # Post-filter
    filtered = []
    for h in hits:
        if source_id and h.get("source_id") != source_id:
            continue
        if scope and h.get("scope") != scope:
            continue
        if chapter and h.get("chapter", "").lower() != chapter.lower():
            continue
        if tags:
            h_tags = set(h.get("tags") or [])
            if not h_tags.intersection(tags):
                continue
        filtered.append(h)

    # Format results
    results = []
    for h in filtered[:top_k]:
        content = h.get("content", "")
        if isinstance(content, dict):
            content = str(content)
        results.append({
            "chunk_id": h.get("chunk_id", h.get("item_id", "")),
            "source_id": h.get("source_id", ""),
            "parent_chunk_id": h.get("parent_chunk_id", ""),
            "title": h.get("title", ""),
            "chapter": h.get("chapter", ""),
            "section": h.get("section", ""),
            "content": content,
            "snippet": content[:400],
            "summary": h.get("summary", ""),
            "score": h.get("_score", 0),
            "scope": h.get("scope", ""),
            "source_type": h.get("source_type", ""),
            "chunk_type": h.get("chunk_type", ""),
            "metadata": h.get("metadata", {}),
            "memory_id": h.get("memory_id", ""),
        })

    return {
        "ok": True,
        "hits": results,
        "total": len(results),
        "metadata": {
            "retrieval_backend": "unified_bm25",
            "source_type_filter": source_type,
        },
    }


# ─── Conversion helpers ───

def _chunk_to_item(d: dict) -> dict:
    """Convert a KnowledgeChunk dict to a ContextStore item dict."""
    chunk_id = d.get("chunk_id", "")
    item_id = f"kc_{chunk_id}" if chunk_id and not chunk_id.startswith("kc_") else chunk_id

    return {
        "item_id": item_id or f"kc_{uuid.uuid4().hex[:12]}",
        "item_type": "knowledge_chunk",
        "source": "knowledge_ingestion",
        "source_id": d.get("source_id", ""),
        "title": d.get("title", "") or d.get("chapter", ""),
        "summary": "",
        "content": d.get("content", ""),
        "scope": d.get("scope", "workspace"),
        "sensitivity": "internal",
        "chunk_id": chunk_id,
        "parent_chunk_id": d.get("parent_chunk_id", ""),
        "chunk_type": d.get("chunk_type", "child"),
        "chapter": d.get("chapter", ""),
        "section": d.get("section", ""),
        "subsection": d.get("subsection", ""),
        "page_start": d.get("page_start"),
        "page_end": d.get("page_end"),
        "chunk_index": d.get("chunk_index", 0),
        "index_text": d.get("index_text", ""),
        "token_count": d.get("token_count", 0),
        "source_type": d.get("source_type", ""),
        "tags": d.get("tags", []),
        "author": d.get("author", ""),
        "language": d.get("language", ""),
        "metadata": d.get("metadata", {}),
    }


def _item_to_chunk(item: dict) -> KnowledgeChunk:
    """Convert a ContextStore item dict to a KnowledgeChunk."""
    return KnowledgeChunk(
        chunk_id=item.get("chunk_id", item.get("item_id", "")),
        source_id=item.get("source_id", ""),
        parent_chunk_id=item.get("parent_chunk_id", ""),
        chunk_type=item.get("chunk_type", "child"),
        chapter=item.get("chapter", ""),
        section=item.get("section", ""),
        subsection=item.get("subsection", ""),
        page_start=item.get("page_start"),
        page_end=item.get("page_end"),
        chunk_index=item.get("chunk_index", 0),
        content=item.get("content", ""),
        index_text=item.get("index_text", ""),
        token_count=item.get("token_count", 0),
        metadata=item.get("metadata", {}),
    )


def _item_to_dict(item: dict) -> dict:
    """Convert a ContextStore item to a public chunk dict."""
    return {
        "chunk_id": item.get("chunk_id", item.get("item_id", "")),
        "source_id": item.get("source_id", ""),
        "parent_chunk_id": item.get("parent_chunk_id", ""),
        "chunk_type": item.get("chunk_type", ""),
        "title": item.get("title", ""),
        "chapter": item.get("chapter", ""),
        "section": item.get("section", ""),
        "content": item.get("content", ""),
        "index_text": item.get("index_text", ""),
        "token_count": item.get("token_count", 0),
        "scope": item.get("scope", ""),
        "enabled": True,
    }
