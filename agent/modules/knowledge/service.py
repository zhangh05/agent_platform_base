# agent/modules/knowledge/service.py
"""Knowledge service.

Public service functions:
  query_knowledge            (high-level wrapper: search + parent
                             expansion + source_summary)
  import_document            (raw text import)
  list_sources
  read_source
  disable_source
  delete_source

  import_file                (file ingestion: parse + chunk)
  list_chunks                (list child/parent chunks)
  search_chunks              (BM25 over child chunks)
  read_chunk                 (read single chunk content)
  read_parent                (read parent chunk via parent_chunk_id)
  reindex_source             (rebuild chunks from source content)

query_knowledge flow:
  1. If the v1.0.1 chunk store has enabled children for the
     workspace, run search_chunks + read_parent for each hit, then
     build a source_summary from the parent context.
  2. Otherwise, query the local source store directly.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Union

logger = logging.getLogger("knowledge.service")


# ── Source service functions ──

def import_document(workspace_id: str, title: str, content: str,
                     source: str = "", metadata: Optional[dict] = None) -> dict:
    from agent.modules.knowledge.store import import_document as _impl
    result = _impl(workspace_id=workspace_id, title=title, content=content,
                   source=source, metadata=metadata)
    if isinstance(result, dict) and "source" in result:
        result["source_id"] = result["source"].get("source_id", "")
    return result


def list_sources(workspace_id: str, include_disabled: bool = False,
                  include_deleted: bool = False, query: str = "",
                  scope: str = "") -> dict:
    from agent.modules.knowledge.store import list_sources as _impl
    result = _impl(workspace_id=workspace_id, include_disabled=include_disabled,
                 include_deleted=include_deleted, query=query, scope=scope)
    sources = result.get("sources", []) if isinstance(result, dict) else []
    return {
        "ok": True,
        "summary": f"Listed {len(sources)} source(s)",
        "sources": sources,
        "count": len(sources),
        "errors": [], "warnings": [],
    }


def read_source(workspace_id: str, source_id: str) -> dict:
    from agent.modules.knowledge.store import read_source as _impl
    rec = _impl(workspace_id=workspace_id, source_id=source_id)
    if rec is None:
        return {
            "ok": False,
            "summary": f"source not found: {source_id}",
            "source_id": source_id,
            "errors": ["source_not_found"],
            "warnings": [],
        }
    return {
        "ok": True,
        "summary": f"Read {rec.get('source_id', '')}",
        "source_id": rec.get("source_id", ""),
        "source": rec,
        "errors": [], "warnings": [],
    }


def disable_source(workspace_id: str, source_id: str,
                    disabled: bool = True) -> dict:
    from agent.modules.knowledge.store import disable_source as _impl
    rec = _impl(workspace_id=workspace_id, source_id=source_id,
                disabled=disabled)
    if rec is None:
        return {
            "ok": False,
            "summary": f"source not found: {source_id}",
            "source_id": source_id,
            "errors": ["source_not_found"],
            "warnings": [],
        }
    return {
        "ok": True,
        "summary": f"Source {source_id} disabled={disabled}",
        "source_id": source_id,
        "source": rec,
        "errors": [], "warnings": [],
    }


def rename_source(workspace_id: str, source_id: str, title: str) -> dict:
    """Rename a knowledge source via the service layer (no direct store access)."""
    from agent.modules.knowledge.store import rename_source as _impl
    rec = _impl(workspace_id=workspace_id, source_id=source_id, title=title)
    if rec is None:
        return {
            "ok": False,
            "summary": f"source not found: {source_id}",
            "source_id": source_id,
            "errors": ["source_not_found"],
            "warnings": [],
        }
    return {
        "ok": True,
        "summary": f"Source {source_id} renamed to '{title}'",
        "source_id": source_id,
        "source": rec,
        "errors": [], "warnings": [],
    }


def delete_source(workspace_id: str, source_id: str) -> dict:
    from agent.modules.knowledge.store import delete_source as _impl
    ok = _impl(workspace_id=workspace_id, source_id=source_id)
    if not ok:
        return {
            "ok": False,
            "summary": f"source not found: {source_id}",
            "source_id": source_id,
            "errors": ["source_not_found"],
            "warnings": [],
        }
    # Also drop the v1.0.1 chunks for this source.
    from agent.modules.knowledge.index import replace_chunks
    replace_chunks(workspace_id, source_id, [])
    return {
        "ok": True,
        "summary": f"Source {source_id} soft-deleted (chunks dropped)",
        "source_id": source_id,
        "errors": [], "warnings": [],
    }


# ── Chunk service functions ──

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
    from agent.modules.knowledge.ingestion import import_file as _impl
    return _impl(
        workspace_id=workspace_id,
        source=source,
        title=title, author=author, edition=edition,
        source_type=source_type, scope=scope, language=language,
        tags=tags, metadata=metadata, file_id=file_id,
    )


def list_chunks(workspace_id: str, source_id: str = "",
                 chunk_type: str = "", limit: int = 200) -> dict:
    from agent.modules.knowledge.index import list_chunks as _impl
    result = _impl(workspace_id=workspace_id, source_id=source_id,
                 chunk_type=chunk_type, limit=limit)
    chunks = result.get("chunks", []) if isinstance(result, dict) else []
    return {
        "ok": True,
        "summary": f"Listed {len(chunks)} chunk(s)",
        "chunks": chunks,
        "count": len(chunks),
        "errors": [], "warnings": [],
    }


def search_chunks(workspace_id: str, query: str, top_k: int = 5,
                    scope: str = "", source_id: str = "",
                    source_type: str = "", tags: Optional[List[str]] = None,
                    chapter: str = "") -> dict:
    from agent.modules.knowledge.index import search_chunks as _impl
    return _impl(
        workspace_id=workspace_id, query=query, top_k=top_k,
        scope=scope, source_id=source_id, source_type=source_type,
        tags=tags, chapter=chapter,
    )


def read_chunk(workspace_id: str, chunk_id: str) -> dict:
    from agent.modules.knowledge.index import get_chunk
    c = get_chunk(workspace_id, chunk_id)
    if c is None:
        return {"ok": False, "summary": f"chunk not found: {chunk_id}",
                "chunk_id": chunk_id,
                "errors": ["chunk_not_found"], "warnings": []}
    return {
        "ok": True,
        "summary": f"Read chunk {chunk_id}",
        "chunk": c.to_dict(),
        "chunk_id": chunk_id,
        "errors": [], "warnings": [],
    }


def read_parent(workspace_id: str, child_chunk_id: str) -> dict:
    """Read the parent chunk of a child chunk, if any."""
    from agent.modules.knowledge.index import get_chunk
    c = get_chunk(workspace_id, child_chunk_id)
    if c is None:
        return {"ok": False,
                "summary": f"child chunk not found: {child_chunk_id}",
                "chunk_id": child_chunk_id,
                "errors": ["chunk_not_found"], "warnings": []}
    pid = c.parent_chunk_id
    if not pid:
        # Self-parent (single child for the section).
        return {
            "ok": True,
            "summary": f"Chunk {child_chunk_id} has no parent (top-level)",
            "parent": c.to_dict(),
            "parent_chunk_id": "",
            "errors": [], "warnings": ["no_parent"],
        }
    p = get_chunk(workspace_id, pid)
    if p is None:
        return {"ok": False,
                "summary": f"parent chunk not found: {pid}",
                "parent_chunk_id": pid,
                "errors": ["parent_not_found"], "warnings": []}
    return {
        "ok": True,
        "summary": f"Read parent {pid} for {child_chunk_id}",
        "parent": p.to_dict(),
        "parent_chunk_id": pid,
        "errors": [], "warnings": [],
    }


def reindex_source(workspace_id: str, source_id: str) -> dict:
    from agent.modules.knowledge.ingestion import reindex_source as _impl
    return _impl(workspace_id=workspace_id, source_id=source_id)


# ── query_knowledge: high-level wrapper ──

def query_knowledge(
    query: str,
    workspace_id: str = "",
    top_k: int = 5,
    filters: Optional[dict] = None,
) -> dict:
    """High-level knowledge query.

    Retrieval flow:
      1. If the chunk store has enabled children for the workspace,
         delegate to search_chunks, then expand each hit to its
         parent for the source_summary view.
      2. Otherwise, query the local source store directly.
    """
    if not query or not query.strip():
        return {
            "ok": False,
            "summary": "请提供查询关键词，例如: [查一下知识库里有没有 SD-WAN 资料]",
            "query": query or "",
            "hits": [],
            "source_count": 0,
            "source_summary": [],
            "warnings": [],
            "errors": ["missing_query"],
            "metadata": {},
        }
    try:
        from storage.ids import validate_workspace_id
        workspace_id = validate_workspace_id(workspace_id)
    except ValueError:
        return {
            "ok": False,
            "summary": "workspace_id is required",
            "query": query.strip(),
            "hits": [],
            "source_count": 0,
            "source_summary": [],
            "warnings": [],
            "errors": ["invalid_workspace_id"],
            "metadata": {},
        }

    from agent.modules.knowledge.index import load_all_chunks
    from agent.modules.knowledge.store import list_sources as _list_sources
    _src_result = _list_sources(workspace_id=workspace_id)
    _sources_list = _src_result.get("sources", []) if isinstance(_src_result, dict) else []
    enabled_source_ids = {s["source_id"] for s in _sources_list
                          if s.get("enabled", True)
                          and not s.get("deleted", False)}
    # Load chunks with a sane safety limit; large knowledge bases should use
    # source-level scoping instead of loading every chunk into memory.
    all_chunks = load_all_chunks(workspace_id)
    if len(all_chunks) > 50_000:
        logger.warning("query_knowledge: too many chunks (%d), truncating to 50000", len(all_chunks))
        all_chunks = all_chunks[:50_000]
    children = [c for c in all_chunks
                if c.chunk_type == "child"
                and c.source_id in enabled_source_ids]
    if children:
        return _query_via_chunks(
            workspace_id=workspace_id, query=query.strip(),
            top_k=top_k, filters=filters,
        )

    from agent.modules.knowledge.store import query as _store_query
    stats_enabled = sum(1 for s in _sources_list
                        if s.get("enabled", True)
                        and not s.get("deleted", False))
    if stats_enabled > 0:
        return _store_query(
            workspace_id=workspace_id, query=query.strip(),
            top_k=top_k, filters=filters,
        )

    return {
        "ok": True,
        "summary": (
            f"知识库中未找到与 '{query.strip()}' 相关的结果。"
            "请先导入文档，或尝试其他关键词。"
        ),
        "query": query.strip(),
        "hits": [],
        "source_count": 0,
        "source_summary": [],
        "warnings": ["store_empty"],
        "errors": [],
        "metadata": {
            "retrieval_backend": "agent.modules.knowledge",
            "workspace_id": workspace_id,
            "top_k": top_k,
        },
    }


def _query_via_chunks(
    workspace_id: str, query: str, top_k: int, filters: Optional[dict],
) -> dict:
    search = search_chunks(
        workspace_id=workspace_id, query=query, top_k=top_k,
        **(filters or {}),
    )
    if not search.get("ok"):
        return search
    hits = search.get("hits") or []
    # For each hit, expand to parent for source_summary.
    expanded = []
    for h in hits:
        pr = read_parent(workspace_id, h["chunk_id"])
        if pr.get("ok"):
            p = pr["parent"]
            expanded.append({
                "chunk_id": h["chunk_id"],
                "parent_chunk_id": h["parent_chunk_id"],
                "title": h["title"],
                "chapter": h["chapter"],
                "section": h["section"],
                "page_start": h.get("page_start"),
                "page_end": h.get("page_end"),
                "score": h.get("score"),
                "snippet": h.get("snippet"),
                "parent_snippet": (p.get("content") or "")[:200],
                "metadata": h.get("metadata") or {},
            })
        else:
            expanded.append({
                "chunk_id": h["chunk_id"],
                "parent_chunk_id": h.get("parent_chunk_id", ""),
                "title": h["title"],
                "chapter": h["chapter"],
                "section": h["section"],
                "page_start": h.get("page_start"),
                "page_end": h.get("page_end"),
                "score": h.get("score"),
                "snippet": h.get("snippet"),
                "parent_snippet": "",
                "metadata": h.get("metadata") or {},
            })
    return {
        "ok": True,
        "summary": search.get("summary", ""),
        "query": query,
        "hits": expanded,
        "source_count": len(expanded),
        "source_summary": search.get("source_summary") or [],
        "warnings": [],
        "errors": [],
        "metadata": dict(search.get("metadata") or {}),
    }


def _build_source_summary(hits: list) -> list:
    if not hits:
        return []
    summaries = []
    for h in hits[:5]:
        content = h.get("content", h.get("llm_safe_content", ""))
        snippet = content[:200] if content else ""
        summaries.append({
            "title": h.get("title", ""),
            "source": h.get("source", ""),
            "score": h.get("score"),
            "snippet": snippet,
        })
    return summaries


# ── v0.8.2 — ModuleResult projection ──

def to_module_result(result: dict) -> "ModuleResult":
    """Project a v1.0 / v1.0.1 result dict into a standard ModuleResult."""
    from agent.protocol.module_result import ModuleResult
    if not isinstance(result, dict):
        return ModuleResult.failure(
            summary="knowledge service returned non-dict result",
            errors=["invalid_result_shape"],
        )
    ok = bool(result.get("ok", False))
    data = {
        "query": result.get("query", ""),
        "hits": list(result.get("hits") or []),
        "source_count": int(result.get("source_count", 0)),
        "source_summary": list(result.get("source_summary") or []),
    }
    if "source_id" in result:
        data["source_id"] = result.get("source_id", "")
    if "source" in result:
        data["source"] = result.get("source", "")
    if "sources" in result:
        data["sources"] = list(result.get("sources") or [])
    if "count" in result:
        data["count"] = int(result.get("count", 0))
    if "chunks" in result:
        data["chunks"] = list(result.get("chunks") or [])
    if "chunk_id" in result:
        data["chunk_id"] = result.get("chunk_id", "")
    if "chunk" in result:
        data["chunk"] = result.get("chunk", "")
    if "parent" in result:
        data["parent"] = result.get("parent", "")
    if "parent_count" in result:
        data["parent_count"] = int(result.get("parent_count", 0))
    if "chunk_count" in result:
        data["chunk_count"] = int(result.get("chunk_count", 0))
    if "format" in result:
        data["format"] = result.get("format", "")
    if "source_type" in result:
        data["source_type"] = result.get("source_type", "")
    if ok:
        return ModuleResult.success(
            summary=str(result.get("summary", "")),
            data=data,
            artifacts=list(result.get("artifacts") or []),
            warnings=list(result.get("warnings") or []),
            metadata=dict(result.get("metadata") or {}),
        )
    return ModuleResult.failure(
        summary=str(result.get("summary", "")),
        errors=list(result.get("errors") or ["unknown_error"]),
        warnings=list(result.get("warnings") or []),
        data=data,
        metadata=dict(result.get("metadata") or {}),
    )
