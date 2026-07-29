# backend/api/knowledge_routes.py
"""Knowledge Index API routes — search, source management."""

from pathlib import Path

from flask import jsonify, request
from storage.ids import validate_workspace_id


KNOWLEDGE_SEARCH_PARAMS = {"workspace_id", "q", "limit", "source_id", "scope"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"}


def _invalid_ws():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _validated_ws_id(raw="default"):
    try:
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_ws()


def _knowledge_file_kind(extension: str) -> str:
    return {
        "txt": "text",
        "md": "markdown",
        "markdown": "markdown",
        "htm": "html",
        "html": "html",
        "yaml": "yaml",
        "yml": "yaml",
        "json": "json",
        "xml": "xml",
        "csv": "csv",
        "log": "log",
        "pdf": "pdf",
        "docx": "docx",
    }.get(extension, "text")


def register_knowledge_routes(app):
    """Register all knowledge API routes on the Flask app."""

    @app.route("/api/knowledge/upload", methods=["POST"])
    def api_knowledge_upload():
        ws_id = request.form.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        if "file" not in request.files:
            return jsonify({"ok": False, "error": "no file provided"}), 400
        uploaded = request.files["file"]
        if not uploaded.filename:
            return jsonify({"ok": False, "error": "empty filename"}), 400

        ext = Path(uploaded.filename).suffix.lower().lstrip(".")
        if ext in IMAGE_EXTENSIONS:
            return jsonify({"ok": False, "error": "unsupported_knowledge_format"}), 400
        file_kind = ext if ext in IMAGE_EXTENSIONS else _knowledge_file_kind(ext)
        binary = ext in IMAGE_EXTENSIONS or ext in {"pdf", "docx"}
        try:
            from storage.file_store import import_user_upload, resolve_file_path

            file_record = import_user_upload(
                workspace_id=ws_id,
                file_source=uploaded.stream,
                original_name=uploaded.filename,
                logical_type="tmp",
                file_kind=file_kind,
                binary=binary,
                source="knowledge_upload",
                metadata={"source_type": request.form.get("source_type", "project_doc")},
            )
            target = resolve_file_path(ws_id, file_record.file_id)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)[:200]}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": "upload_failed", "message": str(exc)[:200]}), 400

        tags = [
            t.strip()
            for t in (request.form.get("tags", "") or "").split(",")
            if t.strip()
        ]
        from agent.modules.knowledge.service import import_file
        result = import_file(
            workspace_id=ws_id,
            source=str(target),
            title=request.form.get("title", "") or uploaded.filename,
            source_type=request.form.get("source_type", "project_doc") or "project_doc",
            scope=request.form.get("scope", "workspace") or "workspace",
            language=request.form.get("language", "zh") or "zh",
            tags=tags,
            metadata={
                "uploaded_filename": uploaded.filename,
            },
        )
        from storage.file_store import purge_file
        purge_file(ws_id, file_record.file_id)

        if not result.get("ok"):
            return jsonify({
                "ok": False,
                "error": (result.get("errors") or ["import_failed"])[0],
                "summary": result.get("summary", "知识库导入失败"),
                "errors": result.get("errors", []),
                "warnings": result.get("warnings", []),
            }), 400

        source = {
            "source_id": result.get("source_id", ""),
            "workspace_id": ws_id,
            "title": result.get("title", "") or request.form.get("title", "") or uploaded.filename,
            "source_type": result.get("source_type", request.form.get("source_type", "project_doc")),
            "scope": result.get("scope", request.form.get("scope", "workspace")),
            "language": result.get("language", request.form.get("language", "zh")),
            "format": result.get("format", ""),
            "chunk_count": result.get("chunk_count", 0),
            "parent_count": result.get("parent_count", 0),
            "status": "indexed",
            "enabled": True,
            "tags": tags,
            "warnings": result.get("warnings", []),
        }
        from storage.events import publish
        publish(ws_id, "knowledge", "created", source["source_id"])
        return jsonify({
            "ok": True,
            "summary": result.get("summary", ""),
            "source": source,
        })

    # ── Source Management ──
    @app.route("/api/knowledge/sources")
    def api_knowledge_sources():
        ws_id = request.args.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        status = request.args.get("status")
        scope = request.args.get("scope", "").strip()
        if scope and scope not in {"workspace", "global", "session"}:
            return jsonify({"ok": False, "error": "invalid_scope"}), 400
        sources = _module_sources(ws_id, status=status, scope=scope)
        # Filter out sources whose artifacts have been deleted
        sources = _filter_deleted_artifact_sources(ws_id, sources)
        for source in sources:
            source.setdefault("status", "indexed" if source.get("enabled", True) else "disabled")
            source.setdefault("chunk_count", 0)
        # Recalculate counts after filtering
        counts = {
            "total": len(sources),
            "indexed": sum(1 for s in sources if s.get("status") == "indexed"),
            "pending": sum(1 for s in sources if s.get("status") == "pending"),
            "indexing": sum(1 for s in sources if s.get("status") == "indexing"),
            "failed": sum(1 for s in sources if s.get("status") == "failed"),
        }
        return jsonify({"ok": True, "sources": sources, "counts": counts})

    @app.route("/api/knowledge/sources/from-artifact", methods=["POST"])
    def api_knowledge_from_artifact():
        data = request.get_json(silent=True) or {}
        ws_id = data.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        artifact_id = data.get("artifact_id", "").strip()
        if not artifact_id:
            return jsonify({"ok": False, "error": "artifact_id required"}), 400

        result = _import_artifact_as_knowledge(ws_id, artifact_id)
        if not result.get("ok"):
            status_code = 404 if result.get("error") == "artifact_not_found" else 400
            return jsonify(result), status_code
        from storage.events import publish
        publish(ws_id, "knowledge", "created", result.get("source", {}).get("source_id", ""))
        return jsonify({"ok": True, "source": result.get("source", {})})

    @app.route("/api/knowledge/sources/<source_id>/reindex", methods=["POST"])
    def api_knowledge_reindex(source_id):
        ws_id = request.args.get("workspace_id", "")
        if request.is_json:
            ws_id = (request.get_json(silent=True) or {}).get("workspace_id", ws_id)
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from agent.modules.knowledge.service import list_sources, reindex_source
        result = reindex_source(ws_id, source_id)
        if result and result.get("ok"):
            from storage.events import publish
            publish(ws_id, "knowledge", "reindexed", source_id)
            all_src = list_sources(ws_id)
            for s in all_src.get("sources", []):
                if s.get("source_id", "") == source_id:
                    return jsonify({"ok": True, "source": s})
            return jsonify({"ok": True, "source": result})
        if result:
            return jsonify(result), 404
        return jsonify({"ok": False, "error": "source_not_found_or_indexing_failed"}), 404

    # ── Delete ──
    @app.route("/api/knowledge/sources/<source_id>", methods=["DELETE"])
    def api_knowledge_delete_source(source_id):
        ws_id = request.args.get("workspace_id", "")
        if request.is_json:
            ws_id = (request.get_json(silent=True) or {}).get("workspace_id", ws_id)
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from agent.modules.knowledge.service import delete_source
        result = delete_source(ws_id, source_id)
        if result.get("ok"):
            from storage.events import publish
            publish(ws_id, "knowledge", "deleted", source_id)
            return jsonify(result)
        return jsonify(result), 404

    # ── Source metadata / availability ──
    @app.route("/api/knowledge/sources/<source_id>", methods=["PATCH"])
    def api_knowledge_rename_source(source_id):
        data = request.get_json(silent=True) or {}
        ws_id = data.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        title_present = "title" in data
        enabled_present = "enabled" in data
        if not title_present and not enabled_present:
            return jsonify({"ok": False, "error": "title or enabled required"}), 400
        from agent.modules.knowledge.service import disable_source, rename_source
        result = None
        if title_present:
            title = str(data.get("title") or "").strip()
            if not title:
                return jsonify({"ok": False, "error": "title required"}), 400
            result = rename_source(ws_id, source_id, title)
            if not result.get("ok"):
                return jsonify({"ok": False, "error": "source_not_found"}), 404
        if enabled_present:
            if not isinstance(data.get("enabled"), bool):
                return jsonify({"ok": False, "error": "enabled must be boolean"}), 400
            result = disable_source(ws_id, source_id, disabled=not data["enabled"])
        if not result.get("ok"):
            return jsonify({"ok": False, "error": "source_not_found"}), 404
        from storage.events import publish
        publish(ws_id, "knowledge", "updated", source_id)
        return jsonify({"ok": True, "source": result.get("source", {})})

    # ── Detail ──
    @app.route("/api/knowledge/sources/<source_id>", methods=["GET"])
    def api_knowledge_get_source(source_id):
        ws_id = request.args.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from agent.modules.knowledge.service import list_chunks, list_sources
        result = list_sources(ws_id, include_disabled=True, include_deleted=True)
        for s in result.get("sources", []):
            sid = s.get("source_id", "")
            if sid == source_id:
                sd = dict(s)
                chunk_result = list_chunks(ws_id, source_id=source_id, limit=50)
                chunks = chunk_result.get("chunks", []) if chunk_result.get("ok") else []
                sd["chunks"] = [_chunk_dict(c) for c in chunks]
                sd["chunk_count"] = len(chunks)
                return jsonify({"ok": True, "source": sd})
        return jsonify({"ok": False, "error": "source_not_found"}), 404

    # ── Search ──
    @app.route("/api/knowledge/search")
    def api_knowledge_search():
        unknown_params = sorted(set(request.args) - KNOWLEDGE_SEARCH_PARAMS)
        if unknown_params:
            return jsonify({
                "ok": False,
                "error": "invalid_query_params",
                "invalid_params": unknown_params,
                "message": "知识库搜索只支持 q、workspace_id、limit、source_id。",
            }), 400
        ws_id = request.args.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        query = request.args.get("q", "").strip()
        source_id = request.args.get("source_id")
        scope = request.args.get("scope", "").strip()
        if scope and scope not in {"workspace", "global", "session"}:
            return jsonify({"ok": False, "error": "invalid_scope"}), 400
        try:
            limit = min(int(request.args.get("limit", 20)), 100)
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_limit"}), 400

        results = _module_search_results(
            workspace_id=ws_id,
            query=query,
            source_id=source_id,
            scope=scope,
            limit=limit,
        )
        return jsonify({
            "ok": True,
            "results": results,
            "count": len(results),
            "query": query,
            "note": "搜索结果为安全摘录，不是完整文件内容。不包含配置详情、密钥或绝对路径。",
        })

    # ── Chunk Detail ──
    @app.route("/api/knowledge/chunks/<chunk_id>")
    def api_knowledge_chunk(chunk_id):
        ws_id = request.args.get("workspace_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        from agent.modules.knowledge.service import read_chunk
        result = read_chunk(ws_id, chunk_id)
        chunk = _module_chunk_to_safe_dict(result.get("chunk", {})) if result.get("ok") else None
        if not chunk:
            return jsonify({"ok": False, "error": "chunk_not_found"}), 404
        # Only return safe fields
        safe = {
            "chunk_id": chunk.get("chunk_id"), "source_id": chunk.get("source_id"),
            "artifact_id": chunk.get("artifact_id"), "title": chunk.get("title"),
            "summary": chunk.get("summary"), "safe_excerpt": chunk.get("safe_excerpt"),
            "sensitivity": chunk.get("sensitivity"),
            "artifact_type": chunk.get("artifact_type"),
            "tags": chunk.get("tags"), "chunk_index": chunk.get("chunk_index"),
            "llm_safe": chunk.get("llm_safe"), "created_at": chunk.get("created_at"),
        }
        return jsonify({"ok": True, "chunk": safe})


def _filter_deleted_artifact_sources(workspace_id: str, sources: list) -> list:
    """Filter out knowledge sources whose artifacts have been deleted.
    
    Ensures knowledge index doesn't show orphaned entries for deleted artifacts.
    """
    try:
        from artifacts.store import get_artifact
    except ImportError:
        return sources
    filtered = []
    for s in sources:
        artifact_id = s.get("artifact_id", "")
        if not artifact_id:
            filtered.append(s)
            continue
        art = get_artifact(workspace_id, artifact_id)
        # Only remove source if artifact exists AND is explicitly deleted
        # If artifact not found, keep source (could be in a test workspace)
        if art is not None and getattr(art, "lifecycle", "active") == "deleted":
            continue
        filtered.append(s)
    return filtered


def _import_artifact_as_knowledge(workspace_id: str, artifact_id: str) -> dict:
    from artifacts.store import get_artifact, read_artifact_content
    from agent.modules.knowledge.service import import_document

    artifact = get_artifact(workspace_id, artifact_id)
    if artifact is None:
        return {"ok": False, "error": "artifact_not_found"}
    art = artifact.as_dict() if hasattr(artifact, "as_dict") else dict(artifact)
    lifecycle = art.get("lifecycle", "active")
    if lifecycle in {"deleted", "quarantined"}:
        return {"ok": False, "error": f"artifact_{lifecycle}"}
    if art.get("sensitivity") == "secret":
        return {"ok": False, "error": "secret_artifact_not_indexable"}
    content = read_artifact_content(workspace_id, artifact_id, allow_sensitive=True)
    if not content:
        return {"ok": False, "error": "artifact_empty"}
    result = import_document(
        workspace_id=workspace_id,
        title=art.get("title") or artifact_id,
        content=content,
        source=f"artifact:{artifact_id}",
        metadata={
            "source_type": "artifact",
            "artifact_id": artifact_id,
            "artifact_type": art.get("artifact_type", ""),
            "scope": art.get("scope", "workspace"),
        },
    )
    if not result.get("ok"):
        return {"ok": False, "error": (result.get("errors") or ["indexing_failed"])[0]}
    source = {
        "source_id": result.get("source_id", ""),
        "title": result.get("title", ""),
        "workspace_id": workspace_id,
    }
    return {"ok": True, "source": source}


def _module_sources(workspace_id: str, status: str = None, scope: str = "") -> list:
    """Return sources from the document knowledge store."""
    try:
        from agent.modules.knowledge.service import list_sources, list_chunks
        src_result = list_sources(workspace_id, scope=scope)
        chunks_result = list_chunks(workspace_id, limit=500)
    except Exception:
        return []
    chunks = chunks_result.get("chunks", []) if isinstance(chunks_result, dict) else []
    child_counts = {}
    for c in chunks:
        if c.get("chunk_type") == "parent":
            continue
        sid = c.get("source_id", "")
        child_counts[sid] = child_counts.get(sid, 0) + 1

    out = []
    for s in src_result.get("sources", []):
        meta = s.get("metadata", {}) or {}
        if meta.get("hidden"):
            continue
        source_status = "indexed" if s.get("enabled", True) and not s.get("deleted", False) else "disabled"
        if status and source_status != status:
            continue
        out.append({
            "source_id": s.get("source_id", ""),
            "workspace_id": workspace_id,
            "title": s.get("title", ""),
            "source_type": meta.get("source_type", s.get("source", "")),
            "status": source_status,
            "enabled": bool(s.get("enabled", True)),
            "chunk_count": child_counts.get(s.get("source_id", ""), 0),
            "language": meta.get("language", ""),
            "tags": list(s.get("tags") or meta.get("tags") or []),
            "created_at": s.get("created_at", ""),
            "updated_at": s.get("updated_at", ""),
            "metadata": {
                "format": s.get("format", "markdown"),
                "source_format": meta.get("format", ""),
                "scope": s.get("scope", meta.get("scope", "workspace")),
                "normalized_file_id": meta.get("normalized_file_id", ""),
            },
        })
    return out


def _module_search_results(
    workspace_id: str, query: str, source_id: str = "", scope: str = "", limit: int = 20,
) -> list:
    try:
        from agent.modules.knowledge.service import search_chunks
        result = search_chunks(
            workspace_id=workspace_id,
            query=query,
            top_k=limit,
            source_id=source_id or "",
            scope=scope,
        )
    except Exception:
        return []
    hits = result.get("hits", []) if isinstance(result, dict) else []
    if not hits and query:
        hits = _module_title_search(workspace_id, query, source_id=source_id, limit=limit)
    if not hits and query:
        return _module_source_store_results(workspace_id, query, source_id=source_id, limit=limit)
    out = []
    for h in hits:
        if (h.get("metadata") or {}).get("hidden"):
            continue
        snippet = h.get("snippet", "")
        out.append({
            "chunk_id": h.get("chunk_id", ""),
            "source_id": h.get("source_id", ""),
            "artifact_id": (h.get("metadata") or {}).get("artifact_id", ""),
            "title": h.get("title", ""),
            "artifact_name": h.get("title", ""),
            "summary": h.get("chapter", "") or h.get("section", ""),
            "safe_excerpt": snippet,
            "artifact_type": "",
            "sensitivity": "internal",
            "tags": list((h.get("metadata") or {}).get("tags") or []),
            "score": round(float(h.get("score", 0) or 0), 3),
            "source_ref": f"knowledge:{h.get('source_id', '')}",
            "llm_safe": True,
        })
    return out


def _module_source_store_results(workspace_id: str, query: str, source_id: str = "", limit: int = 20) -> list:
    try:
        from agent.modules.knowledge.service import query_knowledge
        result = query_knowledge(query=query, workspace_id=workspace_id, top_k=limit)
    except Exception:
        return []
    hits = result.get("hits", []) if isinstance(result, dict) else []
    out = []
    for h in hits:
        meta = h.get("metadata") or {}
        sid = meta.get("source_id", "") or h.get("source", "")
        if source_id and sid != source_id:
            continue
        out.append({
            "chunk_id": meta.get("chunk_id", "") or f"source:{sid}",
            "source_id": sid,
            "artifact_id": meta.get("artifact_id", ""),
            "title": h.get("title", ""),
            "artifact_name": h.get("title", ""),
            "summary": h.get("source", ""),
            "safe_excerpt": (h.get("content", "") or "")[:900],
            "artifact_type": meta.get("artifact_type", ""),
            "sensitivity": "internal",
            "tags": [],
            "score": round(float(h.get("score", 0) or 0), 3),
            "source_ref": f"knowledge:{sid}",
            "llm_safe": True,
        })
        if len(out) >= limit:
            break
    return out


def _module_title_search(workspace_id: str, query: str, source_id: str = "", limit: int = 20) -> list:
    """Supplement document-title/chapter searches.

    The module retriever intentionally filters title-only hits when the body
    has no supporting match. Public library search should still find a
    document by title, so this helper returns safe excerpts from matching
    chunks without exposing full file paths or raw metadata.
    """
    try:
        from agent.modules.knowledge.index import load_all_chunks
        chunks = load_all_chunks(workspace_id)
    except Exception:
        return []
    q = str(query or "").lower().strip()
    if not q:
        return []
    out = []
    for c in chunks:
        if source_id and c.source_id != source_id:
            continue
        if c.chunk_type == "parent":
            continue
        meta = c.metadata or {}
        haystack = " ".join([
            str(meta.get("source_title", "")),
            str(c.chapter or ""),
            str(c.section or ""),
            str(c.subsection or ""),
            str(c.index_text or ""),
        ]).lower()
        if q not in haystack:
            continue
        out.append({
            "chunk_id": c.chunk_id,
            "source_id": c.source_id,
            "parent_chunk_id": c.parent_chunk_id,
            "title": meta.get("source_title", "") or c.chapter,
            "chapter": c.chapter,
            "section": c.section,
            "snippet": (c.content or c.chapter or meta.get("source_title", ""))[:200],
            "score": 0.5,
            "metadata": {
                "source_type": meta.get("source_type", ""),
                "tags": list(meta.get("tags") or []),
            },
        })
        if len(out) >= limit:
            break
    return out


def _chunk_dict(obj) -> dict:
    """Convert a chunk object to a safe dict, handling both dataclass and dict."""
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    if isinstance(obj, dict):
        return obj
    return {}


def _module_chunk_to_safe_dict(chunk: dict) -> dict:
    meta = chunk.get("metadata", {}) or {}
    return {
        "chunk_id": chunk.get("chunk_id"),
        "source_id": chunk.get("source_id"),
        "artifact_id": "",
        "title": meta.get("source_title", ""),
        "summary": chunk.get("chapter", "") or chunk.get("section", ""),
        "safe_excerpt": chunk.get("content", "")[:900],
        "sensitivity": "internal",
        "artifact_type": "",
        "tags": list(meta.get("tags") or []),
        "chunk_index": chunk.get("chunk_index"),
        "llm_safe": True,
        "created_at": chunk.get("created_at"),
    }
