from __future__ import annotations

from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id

from core.tools.general_tools.shared import _caller_workspace, _error_inv, _ok

import re
"""Split general tool handlers."""

def handle_knowledge_index_artifact(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    art_id = args.get("artifact_id", "")
    try:
        validate_workspace_id(ws)
        result = _import_artifact_as_knowledge(ws, art_id)
        if not result.get("ok"):
            return _error_inv(inv, result.get("error", "indexing_failed"))
        source = result.get("source", {})
        return _ok(inv, "", {"indexed": True, "source_id": source.get("source_id", "")})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_knowledge_reindex(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    source_id = args.get("source_id", "")
    try:
        validate_workspace_id(ws)
        from agent.modules.knowledge.service import reindex_source
        result = reindex_source(ws, source_id)
        if not result.get("ok"):
            return _error_inv(inv, str(result.get("error") or "reindex_failed")[:200])
        return _ok(inv, "", {"reindexed": True, "source_id": source_id})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_knowledge_search(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    query = (args.get("query") or "").strip()
    limit = min(int(args.get("limit", 5)), 10)
    try:
        validate_workspace_id(ws)
        from agent.modules.knowledge.service import search_chunks
        result = search_chunks(workspace_id=ws, query=query, top_k=limit)
        if not result.get("ok"):
            errors = result.get("errors") or [result.get("error") or "knowledge_search_failed"]
            return _error_inv(inv, str(errors[0])[:200])
        results = result.get("hits", [])
        safe_results = []
        for r in results:
            d = r.as_dict() if hasattr(r, 'as_dict') else r
            safe_results.append({
                "chunk_id": d.get("chunk_id", ""),
                "title": d.get("title", ""),
                "summary": d.get("chapter", "") or d.get("section", ""),
                "safe_excerpt": d.get("snippet", ""),
                "score": d.get("score", 0),
                "llm_safe": True,
            })
        return _ok(inv, "", {"results": safe_results, "count": len(safe_results)})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_knowledge_list_sources(inv: ToolInvocation) -> dict:
    """List sources instead of routing the list action through search."""
    args = inv.arguments or {}
    ws = _caller_workspace(inv)
    try:
        validate_workspace_id(ws)
        from agent.modules.knowledge.service import list_sources

        return list_sources(
            workspace_id=ws,
            include_disabled=bool(args.get("include_disabled", False)),
            include_deleted=bool(args.get("include_deleted", False)),
            query=str(args.get("query") or ""),
            scope=str(args.get("scope") or ""),
        )
    except Exception as exc:
        return _error_inv(inv, str(exc)[:200])


def handle_knowledge_list_chunks(inv: ToolInvocation) -> dict:
    """List chunks, optionally scoped to a source."""
    args = inv.arguments or {}
    ws = _caller_workspace(inv)
    try:
        validate_workspace_id(ws)
        from agent.modules.knowledge.service import list_chunks

        return list_chunks(
            workspace_id=ws,
            source_id=str(args.get("source_id") or ""),
            chunk_type=str(args.get("chunk_type") or ""),
            limit=max(1, min(int(args.get("limit") or 200), 200)),
        )
    except Exception as exc:
        return _error_inv(inv, str(exc)[:200])

def handle_knowledge_get_source(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    source_id = args.get("source_id", "")
    try:
        validate_workspace_id(ws)
        from agent.modules.knowledge.service import read_source
        result = read_source(ws, source_id)
        if not result.get("ok"):
            return _error_inv(inv, "source not found")
        source = result.get("source", {})
        return _ok(inv, "", {
            "source_id": source.get("source_id", ""),
            "chunk_id": "",
            "title": source.get("title", ""),
            "safe_excerpt": "",
            "source": source.get("source", ""),
            "enabled": source.get("enabled", True),
            "chunk_count": source.get("chunk_count", 0),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_knowledge_get_chunk_summary(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    chunk_id = args.get("chunk_id", "")
    try:
        validate_workspace_id(ws)
        from agent.modules.knowledge.service import read_chunk
        result = read_chunk(ws, chunk_id)
        if not result.get("ok"):
            return _error_inv(inv, "chunk not found")
        chunk = result.get("chunk", {})
        return _ok(inv, "", {
            "chunk_id": chunk_id,
            "source_id": chunk.get("source_id", ""),
            "title": chunk.get("title", ""),
            "summary": chunk.get("chapter", "") or chunk.get("section", ""),
            "safe_excerpt": str(chunk.get("content", ""))[:900],
            "llm_safe": True,
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


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
    return {"ok": True, "source": result.get("source", {})}

def handle_knowledge_explain_not_found(inv: ToolInvocation) -> dict:
    args = inv.arguments
    query = (args.get("query") or "").strip()
    return _ok(inv, "", {
        "message": f"No results found for '{query}'. "
                    "Upload documents in Artifacts and click 'Add to Knowledge Index', "
                    "then try searching again.",
        "suggestion": "upload_and_index",
    })

def handle_runtime_health(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    try:
        from core.runtime.diagnostics import get_diagnostics
        d = get_diagnostics(ws)
        return _ok(inv, "", {"status": "ok", "components": len(d.components)})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_runtime_selfcheck(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    try:
        from core.runtime.selfcheck import run_selfcheck

        result = run_selfcheck(ws)
        payload = result.as_dict()
        selfcheck_status = str(payload.get("status") or "unknown")
        issues = list(payload.get("issues") or [])
        return _ok(inv, "", {
            "selfcheck_status": selfcheck_status,
            "healthy": selfcheck_status == "healthy",
            "issue_count": len(issues),
            "issues": issues[:20],
            "checks": payload.get("checks") or {},
            "message": (
                "selfcheck passed — no issues detected"
                if selfcheck_status == "healthy"
                else f"selfcheck completed with status={selfcheck_status} and {len(issues)} issue(s)"
            ),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_runtime_diagnostics(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    try:
        from core.runtime.diagnostics import get_diagnostics
        d = get_diagnostics(ws)
        comps = []
        for c in d.components:
            comps.append({"name": c.name, "status": c.status, "message": c.message})
        return _ok(inv, "", {"components": comps, "summary": d.summary})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_runtime_local_info(inv: ToolInvocation) -> dict:
    """Return stable local host/network/time facts without shelling out.

    This covers common user requests such as "查看本机 IP 地址" without
    depending on distro-specific commands like ``ip`` or ``ifconfig``.
    """
    import os
    import platform
    import socket
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    timezone_name = str(
        os.environ.get("LZCORE_DISPLAY_TIMEZONE") or "Asia/Shanghai"
    ).strip() or "Asia/Shanghai"
    try:
        display_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = "UTC"
        display_timezone = timezone.utc
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(display_timezone)

    hostname = socket.gethostname()
    fqdn = socket.getfqdn()
    addresses: list[str] = []

    def add_ip(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in addresses:
            addresses.append(value)

    try:
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            add_ip(info[4][0])
    except Exception:
        pass

    primary_ip = ""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            primary_ip = sock.getsockname()[0]
            add_ip(primary_ip)
        finally:
            sock.close()
    except Exception:
        pass

    if not primary_ip:
        primary_ip = next((ip for ip in addresses if not ip.startswith("127.")), "")

    return _ok(inv, "local_info collected", {
        "hostname": hostname,
        "fqdn": fqdn,
        "primary_ip": primary_ip,
        "ipv4_addresses": addresses,
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cwd": os.getcwd(),
        "current_time_utc": now_utc.isoformat(),
        "current_time_local": now_local.isoformat(),
        "local_date": now_local.date().isoformat(),
        "local_timezone": timezone_name,
    })


def handle_runtime_tasks(inv: ToolInvocation) -> dict:
    """List durable runtime tasks for the current workspace."""
    try:
        ws = _caller_workspace(inv)
        args = inv.arguments or {}
        from agent.runtime.durable.store import list_tasks

        tasks = list_tasks(
            ws,
            session_id=str(args.get("session_id") or ""),
            limit=max(1, min(int(args.get("limit") or 50), 200)),
        )
        rows = [task.to_dict() for task in tasks]
        return _ok(inv, f"{len(rows)} durable tasks", {
            "tasks": rows,
            "count": len(rows),
            "workspace_id": ws,
        })
    except Exception as exc:
        return _error_inv(inv, str(exc)[:200])


def handle_runtime_audit_log(inv: ToolInvocation) -> dict:
    """Read the workspace-owned audit log, not generic diagnostics."""
    try:
        ws = _caller_workspace(inv)
        args = inv.arguments or {}
        from storage.audit_store import list_audit_entries

        entries = list_audit_entries(
            ws,
            log_level=str(args.get("log_level") or "info"),
            limit=max(1, min(int(args.get("limit") or 20), 200)),
        )
        return _ok(inv, f"{len(entries)} audit entries", {
            "entries": entries,
            "count": len(entries),
            "workspace_id": ws,
        })
    except Exception as exc:
        return _error_inv(inv, str(exc)[:200])

def handle_runtime_retention_preview(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    try:
        validate_workspace_id(ws)
        return _ok(inv, "", {"candidate_count": 0, "blocked_items": 0, "note": "retention preview only, no apply"})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_runtime_archive_preview(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    try:
        validate_workspace_id(ws)
        from core.runtime.archive import get_archive_audits
        audits = get_archive_audits(ws)
        return _ok(inv, "", {"archive_count": len(audits), "note": "archive preview only"})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_report_render_markdown(inv: ToolInvocation) -> dict:
    content = str(inv.arguments.get("content", ""))
    if len(content) > 10000:
        return _error_inv(inv, "content too large (max 10000 chars)")
    # Check for raw config
    if "interface " in content.lower() and "ip address" in content.lower():
        return _error_inv(inv, "raw config detected — use safe summary only")
    return _ok(inv, "", {"markdown": content[:5000]})

def handle_report_save_artifact(inv: ToolInvocation) -> dict:
    ws = _caller_workspace(inv)
    title = inv.arguments.get("title", "report")
    content = str(inv.arguments.get("content", ""))
    try:
        validate_workspace_id(ws)
        from artifacts.store import save_artifact
        rec = save_artifact(workspace_id=ws, content=content, title=title,
                            artifact_type="report", sensitivity="internal")
        if not rec:
            return _error_inv(inv, "report artifact save blocked or failed")
        return _ok(inv, "", {
            "artifact_id": rec.artifact_id,
            "artifact_ids": [rec.artifact_id],
            "title": title,
            "artifact_type": "report",
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_doc_render_from_safe_summary(inv: ToolInvocation) -> dict:
    summary = str(inv.arguments.get("summary", ""))
    title = inv.arguments.get("title", "document")
    if len(summary) > 5000:
        return _error_inv(inv, "summary too large")
    doc = f"# {title}\n\n{summary}\n\n---\nGenerated by 联智中枢"
    return _ok(inv, "", {"document": doc, "format": "markdown", "title": title})

def handle_table_render_markdown(inv: ToolInvocation) -> dict:
    rows = inv.arguments.get("rows", [])
    headers = inv.arguments.get("headers", [])
    if not rows:
        return _ok(inv, "", {"table": "", "note": "no data"})
    md = "| " + " | ".join(headers or [f"Col{i}" for i in range(len(rows[0]))]) + " |\n"
    md += "|" + "|".join(["---" for _ in range(len(headers or rows[0]))]) + "|\n"
    for row in rows[:50]:
        md += "| " + " | ".join(str(c)[:100] for c in row) + " |\n"
    return _ok(inv, "", {"table": md, "rows": min(len(rows), 50)})

def handle_diagram_render_mermaid(inv: ToolInvocation) -> dict:
    mermaid = str(inv.arguments.get("mermaid", ""))
    if len(mermaid) > 3000:
        return _error_inv(inv, "diagram too large")
    # Only text output, no external rendering
    return _ok(inv, "", {"mermaid": mermaid, "format": "text"})

def handle_text_redact(inv: ToolInvocation) -> dict:
    from core.tools.redaction import redact_tool_output
    text = str(inv.arguments.get("text", ""))
    redacted = redact_tool_output({"text": text})
    return _ok(inv, "", {"redacted": redacted.get("text", ""), "original_length": len(text)})

def handle_text_diff(inv: ToolInvocation) -> dict:
    a = str(inv.arguments.get("text_a", ""))
    b = str(inv.arguments.get("text_b", ""))
    if len(a) > 5000 or len(b) > 5000:
        return _error_inv(inv, "text too large for diff (max 5000 chars each)")
    la, lb = a.splitlines(), b.splitlines()
    diff_lines = []
    for i in range(max(len(la), len(lb))):
        line_a = la[i] if i < len(la) else ""
        line_b = lb[i] if i < len(lb) else ""
        if line_a != line_b:
            diff_lines.append(f"- {line_a[:80]}\n+ {line_b[:80]}")
    return _ok(inv, "", {"diff": "\n".join(diff_lines[:50]), "changed_lines": len(diff_lines)})

def handle_text_extract_keywords(inv: ToolInvocation) -> dict:
    text = str(inv.arguments.get("text", "")).lower()
    words = re.findall(r'\b[a-z\u4e00-\u9fff]{2,}\b', text)
    from collections import Counter
    top = Counter(words).most_common(20)
    return _ok(inv, "", {"keywords": [{"word": w, "count": c} for w, c in top]})

def handle_text_classify(inv: ToolInvocation) -> dict:
    text = str(inv.arguments.get("text", "")).lower()
    categories = {
        "cisco_config": ["cisco", "ios", "interface gigabitethernet"],
        "huawei_config": ["huawei", "interface gigabitethernet", "sysname"],
        "h3c_config": ["h3c", "interface"],
        "general": [],
    }
    scores = {}
    for cat, keywords in categories.items():
        scores[cat] = sum(1 for kw in keywords if kw in text)
    best = max(scores, key=scores.get) if scores else "unknown"
    return _ok(inv, "", {"classification": best, "scores": scores})



__all__ = ['handle_knowledge_index_artifact', 'handle_knowledge_reindex', 'handle_knowledge_search', 'handle_knowledge_get_source', 'handle_knowledge_get_chunk_summary', 'handle_knowledge_explain_not_found', 'handle_runtime_health', 'handle_runtime_selfcheck', 'handle_runtime_diagnostics', 'handle_runtime_retention_preview', 'handle_runtime_archive_preview', 'handle_report_render_markdown', 'handle_report_save_artifact', 'handle_doc_render_from_safe_summary', 'handle_table_render_markdown', 'handle_diagram_render_mermaid', 'handle_text_redact', 'handle_text_diff', 'handle_text_extract_keywords', 'handle_text_classify']
