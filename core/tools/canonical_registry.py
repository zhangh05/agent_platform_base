"""Canonical tool registry for LZCore.

This file is intentionally domain-neutral. Product projects can extend the
registry, but the base exposes only generic runtime, workspace, knowledge,
memory, web, browser, data, report, text, skill, and agent tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.tools.schemas import ToolInvocation, ToolSpec


def handle_weather_current(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.web_tools import handle_weather_current as _impl
    return _impl(inv)


def handle_weather_forecast(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.web_tools import handle_weather_forecast as _impl
    return _impl(inv)


@dataclass(frozen=True)
class CanonicalToolEntry:
    canonical_tool_id: str
    handler: Callable[[ToolInvocation], dict]
    input_schema: dict[str, Any]
    risk_level: str = "low"
    requires_approval: bool = False
    callable_by_llm: bool = True
    enabled: bool = True
    description: str = ""
    permission_action: str = ""
    handler_id: str = ""
    execution_contract: dict[str, Any] | None = None


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties or {}, "required": required or []}


def _action(inv: ToolInvocation) -> str:
    return str((inv.arguments or {}).get("action") or "").lower().strip()


def _unsupported(inv: ToolInvocation, actions: str) -> dict:
    return {"ok": False, "error": f"unsupported action for {inv.tool_id}: {_action(inv) or '<missing>'}; expected {actions}"}


def _local_glob(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.shared import _caller_workspace, _workspace_path

    args = inv.arguments or {}
    pattern = str(args.get("pattern") or "*")
    subdir = str(args.get("subdir") or "")
    limit = int(args.get("limit") or 200)
    root = _workspace_path(_caller_workspace(inv), subdir)
    matches = []
    for path in root.glob(pattern):
        if len(matches) >= limit:
            break
        matches.append({
            "path": str(path.relative_to(root)),
            "is_file": path.is_file(),
            "size": path.stat().st_size if path.is_file() else 0,
        })
    return {
        "ok": True,
        "matches": matches,
        "count": len(matches),
        "summary": f"Matched {len(matches)} workspace path(s).",
    }


def _local_delete(inv: ToolInvocation) -> dict:
    """Move a workspace file to trash and synchronize an exact managed record.

    This remains the canonical ``workspace.file`` handler. A managed FileStore
    payload may have a data-center projection; leaving it active after moving
    the payload caused stale, unreadable entries. Only one exact active path
    match is projected; an ambiguous index is rejected before any file move.
    """
    from core.tools.general_tools.shared import _caller_workspace, _workspace_path
    from storage import index as file_index

    args = inv.arguments or {}
    filepath = str(args.get("filepath") or args.get("path") or "").strip()
    if not filepath:
        return {"ok": False, "error": "filepath is required"}
    ws = _caller_workspace(inv)
    target = _workspace_path(ws, filepath)
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": "file not found"}

    canonical_path = target.relative_to(_workspace_path(ws)).as_posix()
    managed_records = [
        record for record in file_index.read_file_records(ws)
        if str(record.get("path") or "") == canonical_path
        and str(record.get("lifecycle") or "active") == "active"
    ]
    if len(managed_records) > 1:
        return {"ok": False, "error": "managed_file_index_ambiguous"}

    trash = _workspace_path(ws, ".trash")
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / target.name
    i = 1
    while dest.exists():
        dest = trash / f"{target.stem}.{i}{target.suffix}"
        i += 1
    target.rename(dest)
    trash_path = dest.relative_to(_workspace_path(ws)).as_posix()

    managed_file_id = ""
    if managed_records:
        from storage.file_store import soft_delete_file

        managed = managed_records[0]
        managed_file_id = str(managed.get("file_id") or "")
        if not managed_file_id or not soft_delete_file(ws, managed_file_id):
            try:
                dest.rename(target)
            except OSError:
                return {
                    "ok": False,
                    "error": "managed_file_lifecycle_sync_failed_rollback_failed",
                }
            return {"ok": False, "error": "managed_file_lifecycle_sync_failed"}
        refreshed = next(
            (record for record in file_index.read_file_records(ws)
             if str(record.get("file_id") or "") == managed_file_id),
            {},
        )
        metadata = dict(refreshed.get("metadata") or {})
        metadata.update({
            "trash_path": trash_path,
            "deleted_by": "workspace.file",
            "deleted_run_id": str(inv.run_id or ""),
            "deleted_session_id": str(inv.session_id or ""),
        })
        trash_projection_ok = file_index.update_file_record(
            ws, managed_file_id, {"metadata": metadata},
        )
    else:
        trash_projection_ok = True

    result = {
        "ok": True,
        "deleted": True,
        "trash_path": trash_path,
        "managed_file_id": managed_file_id,
        "summary": f"Moved {canonical_path} to workspace trash.",
    }
    if not trash_projection_ok:
        result["warnings"] = ["managed_file_trash_projection_failed"]
    return result
def _handle_exec(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.command_tools import (
        handle_command_approved_exec,
        handle_powershell_approved_script,
        handle_python_exec,
        handle_slash_run,
    )

    action = _action(inv) or "shell"
    if action == "python":
        return handle_python_exec(inv)
    if action == "slash":
        return handle_slash_run(inv)
    if action == "shell":
        if str((inv.arguments or {}).get("target") or "local").lower() != "local":
            return {"ok": False, "error": "联智中枢仅支持本地执行"}
        if str((inv.arguments or {}).get("shell") or "").lower() == "powershell":
            return handle_powershell_approved_script(inv)
        return handle_command_approved_exec(inv)
    return _unsupported(inv, "shell|python|slash")


def _handle_browser(inv: ToolInvocation) -> dict:
    args = inv.arguments or {}
    action = _action(inv) or "navigate"
    from agent.modules.browser import core as browser

    if action == "navigate":
        return browser.browser_navigate(str(args.get("url") or ""), wait_selector=str(args.get("wait_selector") or ""), timeout=int(args.get("timeout") or 30000))
    if action == "snapshot":
        return browser.browser_snapshot(selector=str(args.get("selector") or "body"), compact=bool(args.get("compact", True)), max_elements=int(args.get("max_elements") or 50))
    if action == "screenshot":
        return browser.browser_screenshot(url=str(args.get("url") or ""), full_page=bool(args.get("full_page", False)), as_file=bool(args.get("as_file", True)), workspace_id=str(args.get("workspace_id") or inv.workspace_id or ""))
    if action == "click":
        return browser.browser_click(str(args.get("selector") or ""), ref=str(args.get("ref") or ""))
    if action == "type":
        return browser.browser_type(str(args.get("text") or ""), selector=str(args.get("selector") or ""), ref=str(args.get("ref") or ""), clear_first=bool(args.get("clear_first", True)))
    if action == "extract":
        return browser.browser_extract(str(args.get("url") or ""), selector=str(args.get("selector") or "body"))
    if action == "scroll":
        return browser.browser_scroll(str(args.get("direction") or "down"), int(args.get("amount") or 500))
    if action == "hover":
        return browser.browser_hover(str(args.get("selector") or ""), ref=str(args.get("ref") or ""))
    if action == "press_key":
        return browser.browser_press_key(str(args.get("key") or ""))
    if action == "select_option":
        return browser.browser_select_option(str(args.get("value") or ""), selector=str(args.get("selector") or ""), ref=str(args.get("ref") or ""))
    if action == "evaluate":
        return browser.browser_evaluate(str(args.get("script") or ""))
    if action == "wait":
        return browser.browser_wait(wait_ms=int(args.get("wait_ms") or 0), wait_text=str(args.get("wait_text") or ""), timeout=int(args.get("timeout") or 30000))
    if action == "tabs":
        return browser.browser_tabs(action=str(args.get("tab_action") or args.get("tab_action") or "list"), url=str(args.get("url") or ""), tab_index=int(args.get("tab_index") or 0))
    if action == "network":
        return browser.browser_network()
    if action == "console":
        return browser.browser_console()
    if action == "navigate_back":
        return browser.browser_navigate_back()
    if action == "close":
        return browser.browser_close()
    return _unsupported(inv, "navigate|snapshot|screenshot|click|type|extract|scroll|hover|press_key|select_option|evaluate|wait|tabs|network|console|navigate_back|close")


def _handle_web(inv: ToolInvocation) -> dict:
    args = inv.arguments or {}
    action = _action(inv) or "search"
    source = str(args.get("source") or "").lower()
    if action in {"search", "list"}:
        from core.tools.general_tools.web_tools import handle_news_search, handle_web_official_doc_search, handle_web_search
        if source == "docs":
            return handle_web_official_doc_search(inv)
        if source == "news":
            return handle_news_search(inv)
        return handle_web_search(inv)
    if action == "weather":
        days = int(args.get("days") or 1)
        return handle_weather_forecast(inv) if days > 1 else handle_weather_current(inv)
    if action == "weather_batch":
        from core.tools.general_tools.web_tools import handle_weather_batch
        return handle_weather_batch(inv)
    if action == "fetch":
        from core.tools.general_tools.web_content import fetch_and_extract
        return fetch_and_extract(
            url=str(args.get("url") or ""),
            extract_mode=str(args.get("extract_mode") or "article"),
            max_length=int(args.get("max_length") or 15000),
            timeout=int(args.get("timeout") or 15),
            workspace_id=str(args.get("workspace_id") or inv.workspace_id or "default"),
        )
    if action == "deep_search":
        from core.tools.general_tools.web_content import fetch_with_fallback

        top_k = max(1, min(int(args.get("top_k") or 3), 5))
        search_result = _handle_web(ToolInvocation(
            tool_id=inv.tool_id,
            arguments={**args, "action": "search", "depth": "deep", "max_results": max(top_k, int(args.get("max_results") or top_k))},
            workspace_id=inv.workspace_id,
        ))
        if not search_result.get("ok", False):
            return search_result
        pages = []
        for item in list(search_result.get("results") or [])[:top_k]:
            fetched = fetch_with_fallback(
                str(item.get("url") or ""),
                workspace_id=str(inv.workspace_id or "default"),
                max_length=int(args.get("max_length") or 20000),
                timeout=int(args.get("timeout") or 15),
            )
            pages.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
                "citation": item.get("citation", ""),
                "source_quality": item.get("source_quality", "unknown"),
                "ok": bool(fetched.get("ok", False)),
                "content": fetched.get("content", ""),
                "content_length": fetched.get("content_length", 0),
                "quality_score": fetched.get("quality_score", 0),
                "error": fetched.get("error", ""),
            })
        usable = sum(1 for page in pages if page["ok"] and page["content"])
        return {
            "ok": usable > 0,
            "summary": f"Deep search fetched {usable}/{len(pages)} source page(s).",
            "query": args.get("query", ""),
            "search_results": search_result.get("results", []),
            "authority": search_result.get("authority", {}),
            "pages": pages,
            "count": usable,
            "errors": [] if usable else ["no source page could be fetched"],
            "answer_hint": (
                "Base precise claims only on successfully fetched page content; "
                "cite each supporting source title and URL and disclose unfetched or conflicting candidates."
            ),
        }
    return _unsupported(inv, "search|fetch|weather|deep_search")


def _handle_data(inv: ToolInvocation) -> dict:
    from core.tools.general_tools import data_engine

    args = inv.arguments or {}
    action = _action(inv) or "parse"
    text = str(args.get("text") or "")
    rows = args.get("rows")
    def max_rows() -> int:
        # The schema normally guarantees an integer. Keep this boundary
        # deterministic when a malformed tool call reaches it nonetheless.
        try:
            return int(args.get("max_rows") or 50)
        except (TypeError, ValueError):
            return 50
    if action == "parse":
        return data_engine.data_parse(text=text, rows=rows)
    if action == "stats":
        return data_engine.data_stats(text=text, rows=rows)
    if action == "distinct":
        return data_engine.data_distinct(text=text, rows=rows, column=str(args.get("column") or ""))
    if action == "aggregate":
        return data_engine.data_aggregate(text=text, rows=rows, group_by=args.get("group_by"), metrics=args.get("metrics"))
    if action == "filter":
        return data_engine.data_filter(text=text, rows=rows, conditions=args.get("conditions"), max_rows=max_rows())
    if action == "sort":
        return data_engine.data_sort(text=text, rows=rows, by=args.get("by"), order=str(args.get("order") or "asc"), max_rows=max_rows())
    if action == "render":
        return data_engine.data_render(text=text, rows=rows, output=str(args.get("output") or "markdown"), max_rows=max_rows())
    if action == "pivot":
        return data_engine.data_pivot(text=text, rows=rows, index=str(args.get("index") or ""), columns=str(args.get("columns") or args.get("pivot_columns") or ""), values=str(args.get("values") or args.get("pivot_values") or ""), aggfunc=str(args.get("aggfunc") or "sum"))
    if action == "join":
        return data_engine.data_join(text=text, rows=rows, right_text=str(args.get("right_text") or ""), right_rows=args.get("right_rows"), on=str(args.get("on") or ""), how=str(args.get("how") or "inner"))
    return _unsupported(inv, "parse|stats|distinct|aggregate|filter|sort|render|pivot|join")


def _handle_report(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.runtime_tools import handle_doc_render_from_safe_summary, handle_report_save_artifact, handle_text_diff

    action = _action(inv) or "document"
    if action == "save":
        return handle_report_save_artifact(inv)
    if action == "diff":
        return handle_text_diff(inv)
    if action == "document":
        return handle_doc_render_from_safe_summary(inv)
    return _unsupported(inv, "save|diff|document")


def _handle_knowledge(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.runtime_tools import (
        handle_knowledge_get_chunk_summary,
        handle_knowledge_get_source,
        handle_knowledge_index_artifact,
        handle_knowledge_list_chunks,
        handle_knowledge_list_sources,
        handle_knowledge_reindex,
        handle_knowledge_search,
    )

    action = _action(inv) or "search"
    level = str((inv.arguments or {}).get("level") or "chunk").lower()
    if action == "search":
        return handle_knowledge_search(inv)
    if action == "read":
        has_source = bool((inv.arguments or {}).get("source_id"))
        return handle_knowledge_get_source(inv) if level == "source" or has_source else handle_knowledge_get_chunk_summary(inv)
    if action == "import":
        return handle_knowledge_index_artifact(inv)
    if action in {"reindex", "manage"}:
        return handle_knowledge_reindex(inv)
    if action == "list":
        return handle_knowledge_list_sources(inv)
    if action == "chunk":
        return handle_knowledge_list_chunks(inv)
    return _unsupported(inv, "search|read|list|chunk|import|reindex")


def _handle_memory(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.memory_tools import (
        handle_memory_confirm,
        handle_memory_create,
        handle_memory_delete_soft,
        handle_memory_get_profile,
        handle_memory_review,
        handle_memory_search,
        handle_memory_set_profile,
        handle_memory_update,
    )

    action = _action(inv) or "search"
    return {
        "search": handle_memory_search,
        "create": handle_memory_create,
        "update": handle_memory_update,
        "confirm": handle_memory_confirm,
        "delete": handle_memory_delete_soft,
        "review": handle_memory_review,
        "profile_get": handle_memory_get_profile,
        "profile_set": handle_memory_set_profile,
    }.get(action, lambda x: _unsupported(x, "search|create|update|confirm|delete|review|profile_get|profile_set"))(inv)


def _handle_skill(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.skill_tools import handle_mcp_call, handle_mcp_list_tools, handle_skill_find, handle_skill_inspect, handle_skill_list, handle_skill_load

    action = _action(inv) or "list"
    return {
        "list": handle_skill_list,
        "find": handle_skill_find,
        "search": handle_skill_find,
        "load": handle_skill_load,
        "inspect": handle_skill_inspect,
        "mcp_list_tools": handle_mcp_list_tools,
        "mcp_call": handle_mcp_call,
    }.get(action, lambda x: _unsupported(x, "list|search|load|inspect|mcp_list_tools|mcp_call"))(inv)


def _handle_agent(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.agent_tools import handle_agent_cancel, handle_agent_get_result, handle_agent_list, handle_agent_merge, handle_agent_spawn, handle_agent_status

    action = _action(inv) or "list"
    return {
        "spawn": handle_agent_spawn,
        "list": handle_agent_list,
        "get": handle_agent_get_result,
        "cancel": handle_agent_cancel,
        "status": handle_agent_status,
        "merge": handle_agent_merge,
    }.get(action, lambda x: _unsupported(x, "spawn|list|get|cancel|status|merge"))(inv)


def _handle_system(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.runtime_tools import (
        handle_runtime_audit_log,
        handle_runtime_diagnostics,
        handle_runtime_health,
        handle_runtime_local_info,
        handle_runtime_selfcheck,
        handle_runtime_tasks,
    )
    from core.tools.general_tools.session_tools import (
        handle_run_get_merged,
        handle_session_checkpoint,
        handle_session_export,
        handle_session_get_merged,
        handle_session_rewind,
        handle_session_snapshot,
    )

    action = _action(inv) or "diagnostics"
    return {
        "diagnostics": handle_runtime_diagnostics,
        "health": handle_runtime_health,
        "selfcheck": handle_runtime_selfcheck,
        "local_info": handle_runtime_local_info,
        "tasks": handle_runtime_tasks,
        "audit_log": handle_runtime_audit_log,
        "run_get": handle_run_get_merged,
        "session_get": handle_session_get_merged,
        "session_checkpoint": handle_session_checkpoint,
        "session_rewind": handle_session_rewind,
        "session_export": handle_session_export,
        "session_snapshot": handle_session_snapshot,
    }.get(action, lambda x: _unsupported(x, "diagnostics|health|selfcheck|local_info|tasks|audit_log|run_get|session_get|session_checkpoint|session_rewind|session_export|session_snapshot"))(inv)


def _handle_text(inv: ToolInvocation) -> dict:
    import re
    from core.tools.general_tools.runtime_tools import handle_text_extract_keywords, handle_text_redact

    args = inv.arguments or {}
    action = _action(inv) or "redact"
    if action == "redact":
        return handle_text_redact(inv)
    if action in {"extract", "extract_entities"}:
        return handle_text_extract_keywords(inv)
    if action == "match":
        pattern = str(args.get("pattern") or "")
        if not pattern:
            return {"ok": False, "error": "pattern is required"}
        try:
            matches = re.findall(pattern, str(args.get("text") or ""), re.MULTILINE | re.DOTALL)
        except re.error as exc:
            return {"ok": False, "error": f"invalid regex: {exc}"}
        return {
            "ok": True,
            "matches": matches[:100],
            "match_count": len(matches),
            "summary": f"Regex matched {len(matches)} occurrence(s).",
        }
    return _unsupported(inv, "redact|extract|extract_entities|match")


def _handle_workspace_file(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.filestore_tools import handle_file_extract_document, handle_file_extract_document_image, handle_file_extract_document_images
    from core.tools.general_tools.file_tools import (
        handle_file_edit,
        handle_file_patch,
        handle_file_read,
        handle_file_read_image,
        handle_ws_list_files,
        handle_ws_write_artifact_file,
    )

    action = _action(inv) or "list"
    return {
        "list": handle_ws_list_files,
        "read": handle_file_read,
        "read_image": handle_file_read_image,
        "extract_document": handle_file_extract_document,
        "extract_document_image": handle_file_extract_document_image,
        "extract_document_images": handle_file_extract_document_images,
        "edit": handle_file_edit,
        "patch": handle_file_patch,
        "write": handle_ws_write_artifact_file,
        "write_artifact": handle_ws_write_artifact_file,
        "glob": _local_glob,
        "delete": _local_delete,
    }.get(action, lambda x: _unsupported(x, "list|read|read_image|extract_document|extract_document_image|extract_document_images|edit|patch|write|write_artifact|glob|delete"))(inv)


def _handle_workspace_artifact(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.artifact_tools import (
        handle_artifact_delete_soft,
        handle_artifact_read_content_safe,
        handle_artifact_save_result,
        handle_artifact_search,
        handle_artifact_tag,
    )

    action = _action(inv) or "list"
    return {
        "list": handle_artifact_search,
        "read": handle_artifact_read_content_safe,
        "save": handle_artifact_save_result,
        "tag": handle_artifact_tag,
        "delete": handle_artifact_delete_soft,
    }.get(action, lambda x: _unsupported(x, "list|read|save|tag|delete"))(inv)


def _handle_workspace_filestore(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.filestore_tools import (
        handle_file_import_workspace_path,
        handle_file_reconcile_trash,
        handle_file_references,
    )

    action = _action(inv) or "references"
    if action == "references":
        return handle_file_references(inv, file_id=str((inv.arguments or {}).get("file_id") or ""))
    if action == "import":
        return handle_file_import_workspace_path(inv, filepath=str((inv.arguments or {}).get("filepath") or ""))
    if action == "reconcile_trash_preview":
        return handle_file_reconcile_trash(inv, apply=False)
    if action == "reconcile_trash":
        return handle_file_reconcile_trash(inv, apply=True)
    return _unsupported(inv, "references|import|reconcile_trash_preview|reconcile_trash")


def _handle_workspace_metadata(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.file_tools import handle_ws_get_metadata
    return handle_ws_get_metadata(inv)


def _handle_pdf_extract(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.pdf_tools import handle_pdf_extract_text
    return handle_pdf_extract_text(inv)


def _weather_merged(inv: ToolInvocation) -> dict:
    result = _handle_web(inv)
    return {
        "ok": bool(result.get("ok", True)),
        "status": result.get("status", "ok" if result.get("ok", True) else "failed"),
        "output": result,
    }


def _entry(
    tool_id: str,
    handler: Callable[[ToolInvocation], dict],
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    risk: str = "low",
    permission: str = "",
    description: str = "",
    reads_artifact: bool = False,
    writes_artifact: bool = False,
    execution_contract: dict[str, Any] | None = None,
) -> CanonicalToolEntry:
    return CanonicalToolEntry(
        canonical_tool_id=tool_id,
        handler=handler,
        input_schema=_schema(properties, required),
        risk_level=risk,
        permission_action=permission,
        description=description,
        handler_id=tool_id,
        execution_contract=execution_contract,
    )


_COMMON = {
    "workspace_id": {"type": "string", "description": "Current workspace id; normally supplied by runtime."},
}

_EXEC_ARGS = {
    "command": {"type": "string", "description": "Shell/slash command; required for action=shell|slash."},
    "code": {"type": "string", "description": "Python source; required for action=python."},
    "input_data": {
        "type": "object",
        "description": "Structured data supplied to action=python as the input_data variable; may be bound from a prior tool step.",
    },
    "description": {"type": "string"},
    "working_dir": {"type": "string", "description": "Workspace-relative working directory."},
    "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
    "target": {"type": "string", "enum": ["local"], "default": "local"},
    "shell": {"type": "string", "enum": ["cmd", "powershell"], "default": "cmd"},
    "env_vars": {"type": "object"},
}

_BROWSER_ARGS = {
    "url": {"type": "string"}, "selector": {"type": "string"},
    "ref": {"type": "string"}, "text": {"type": "string"},
    "script": {"type": "string"}, "key": {"type": "string"},
    "value": {"type": "string"}, "wait_selector": {"type": "string"},
    "wait_text": {"type": "string"}, "timeout": {"type": "integer", "minimum": 1},
    "wait_ms": {"type": "integer", "minimum": 0},
    "compact": {"type": "boolean"}, "max_elements": {"type": "integer", "minimum": 1},
    "full_page": {"type": "boolean"}, "as_file": {"type": "boolean"},
    "clear_first": {"type": "boolean"},
    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
    "amount": {"type": "integer", "minimum": 1},
    "tab_action": {"type": "string", "enum": ["list", "new", "switch", "close"]},
    "tab_index": {"type": "integer", "minimum": 0},
}

_WEB_ARGS = {
    "query": {"type": "string", "description": "Search query for action=search|deep_search."},
    "source": {"type": "string", "enum": ["web", "news", "docs"]},
    "authority_profile": {
        "type": "string",
        "enum": ["auto", "general_web", "official_docs", "network_vendor", "protocol_standard", "security_advisory"],
        "description": "Evidence-source policy. Use auto unless the claim clearly needs official docs, vendor docs, standards, or security advisories.",
    },
    "url": {"type": "string"}, "location": {
        "type": "string",
        "description": (
            "One precise weather location per call. For a region or an all/every request, first resolve an "
            "explicit location set; never silently replace the requested scope with representative cities."
        ),
    },
    "locations": {
        "type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 10,
        "description": "Two to ten explicit weather locations for one bounded batch lookup.",
    },
    "days": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Forecast horizon in days (1-10)."},
    "language": {"type": "string"}, "units": {"type": "string", "enum": ["metric", "imperial"]},
    "recency": {"type": "string"},
    "safe_search": {"type": "string", "enum": ["strict", "moderate", "off"]},
    "depth": {"type": "string", "enum": ["fast", "balanced", "deep"]},
    "domains": {"type": "array", "items": {"type": "string"}},
    "allowed_domains": {"type": "array", "items": {"type": "string"}},
    "blocked_domains": {"type": "array", "items": {"type": "string"}},
    "site": {"type": "string"}, "vendor": {"type": "string"},
    "max_results": {"type": "integer", "minimum": 1}, "top_k": {"type": "integer", "minimum": 1},
    "extract_mode": {"type": "string", "enum": ["article", "full", "structured", "links"]},
    "max_length": {"type": "integer", "minimum": 1}, "timeout": {"type": "integer", "minimum": 1},
}

_DATA_ARGS = {
    "rows": {"type": "array"}, "text": {"type": "string"},
    "column": {"type": "string"}, "conditions": {"type": "array"},
    "group_by": {"type": "array", "items": {"type": "string"}}, "metrics": {"type": "array"},
    "by": {"type": "array", "items": {"type": "string"}}, "order": {"type": "string", "enum": ["asc", "desc"]},
    "max_rows": {"type": "integer", "minimum": 1, "maximum": 200},
    "output": {"type": "string", "enum": ["markdown", "json", "csv"]},
    "index": {"type": "string"}, "columns": {"type": "string"},
    "values": {"type": "string"}, "aggfunc": {"type": "string", "enum": ["sum", "count", "avg", "min", "max"]},
    "right_text": {"type": "string"}, "right_rows": {"type": "array"},
    "on": {"type": "string"}, "how": {"type": "string", "enum": ["inner", "left"]},
}

_MEMORY_ARGS = {
    "query": {"type": "string"},
    "limit": {"type": "integer", "minimum": 1},
    "title": {"type": "string"},
    "content": {"type": "string"},
    "memory_id": {"type": "string"}, "memory_type": {"type": "string"},
    "scope": {"type": "string"}, "field": {"type": "string"},
    "value": {}, "merge": {"type": "boolean"}, "session_id": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
}

_SYSTEM_ARGS = {
    "run_id": {"type": "string"}, "session_id": {"type": "string"},
    "snapshot_id": {"type": "string"}, "log_level": {"type": "string"},
    "operation": {"type": "string"}, "reason": {"type": "string"},
    "format": {"type": "string", "enum": ["json", "markdown"]},
    "dry_run": {"type": "boolean"}, "status": {"type": "string"},
}

_WORKSPACE_FILE_ARGS = {
    "filepath": {"type": "string", "description": "Workspace-relative path for read/edit/patch/delete."},
    "content": {"type": "string", "description": "Content for write/write_artifact."},
    "limit": {"type": "integer", "minimum": 1},
    "offset": {"type": "integer", "minimum": 0}, "subdir": {"type": "string"},
    "pattern": {"type": "string"}, "old_string": {"type": "string"},
    "new_string": {"type": "string"}, "replace_all": {"type": "boolean"},
    "patch_text": {"type": "string"}, "filename": {"type": "string"},
    "dry_run": {"type": "boolean"},
    "file_id": {"type": "string", "description": "Managed attachment id for extract_document and embedded-image extraction actions; never use it as filepath."},
    "image_index": {"type": "integer", "minimum": 1, "description": "1-based embedded DOCX image index."},
    "start_index": {"type": "integer", "minimum": 1, "description": "First 1-based DOCX image index for a batch."},
}


_RAW_REGISTRY: list[CanonicalToolEntry] = [
    _entry("exec.run", _handle_exec, {
        **_COMMON, **_EXEC_ARGS,
        "action": {"type": "string", "enum": ["shell", "python", "slash"], "default": "shell"},
    }, required=["action"], risk="medium", permission="exec", description="Local shell, slash, and Python data processing. Python uses the policy-selected runner: trusted local mode is explicitly best-effort, while network or multi-user mode requires strong container isolation."),
    _entry("browser.manage", _handle_browser, {**_COMMON, **_BROWSER_ARGS, "action": {"type": "string", "enum": ["navigate", "snapshot", "screenshot", "click", "type", "extract", "scroll", "hover", "press_key", "select_option", "evaluate", "wait", "tabs", "network", "console", "navigate_back", "close"]}}, required=["action"], risk="medium", description="Browser automation. navigate/extract require url; click/hover require selector or ref; type requires text and selector/ref."),
    _entry("web.manage", _handle_web, {**_COMMON, **_WEB_ARGS, "action": {"type": "string", "enum": ["search", "fetch", "weather", "weather_batch", "deep_search"]}}, required=["action"], description="Current external evidence via search/fetch/weather. Use proactively for time-sensitive facts, official technical references, versions and vulnerabilities. search finds candidates; fetch verifies page content; deep_search does both for top sources. Weather accepts one precise location; weather_batch accepts 2-10 explicit locations. Resolve broad/all scopes explicitly and report exact coverage rather than silently choosing representative locations. Select authority_profile and cite returned titles/URLs.", execution_contract={
        "batching": [{
            "source_action": "weather",
            "target_action": "weather_batch",
            "group_by": ["days", "language", "units"],
            "collect_arg": "location",
            "collection_arg": "locations",
            "max_batch_size": 10,
        }],
    }),
    _entry("data.manage", _handle_data, {**_COMMON, **_DATA_ARGS, "action": {"type": "string", "enum": ["parse", "stats", "distinct", "aggregate", "filter", "sort", "render", "pivot", "join"]}}, required=["action"], description="Structured data processing. Supply text or rows; action-specific columns/options are declared in the schema."),
    _entry("report.manage", _handle_report, {**_COMMON, "action": {"type": "string", "enum": ["save", "diff", "document"]}, "title": {"type": "string"}, "content": {"type": "string"}, "summary": {"type": "string"}, "text_a": {"type": "string"}, "text_b": {"type": "string"}}, required=["action"], description="Report operations. save requires content; diff requires text_a/text_b; document requires summary."),
    _entry("knowledge.manage", _handle_knowledge, {**_COMMON, "action": {"type": "string", "enum": ["search", "read", "list", "chunk", "import", "reindex"]}, "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}, "artifact_id": {"type": "string"}, "level": {"type": "string", "enum": ["chunk", "source"]}, "chunk_id": {"type": "string"}, "source_id": {"type": "string"}, "chunk_type": {"type": "string"}, "scope": {"type": "string"}, "include_disabled": {"type": "boolean"}, "include_deleted": {"type": "boolean"}}, required=["action"], risk="medium", description="Knowledge operations. search requires query; read requires chunk_id or source_id; list lists sources; chunk lists chunks; import requires artifact_id; reindex requires source_id."),
    _entry("memory.manage", _handle_memory, {**_COMMON, **_MEMORY_ARGS, "action": {"type": "string", "enum": ["search", "review", "confirm", "create", "update", "delete", "profile_get", "profile_set"]}}, required=["action"], risk="medium", description="Memory operations. create requires content; update/confirm/delete require memory_id; profile_set requires field and value."),
    _entry("skill.manage", _handle_skill, {**_COMMON, "action": {"type": "string", "enum": ["list", "find", "load", "inspect", "mcp_list_tools", "mcp_call"]}, "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}, "skill_name": {"type": "string"}, "provider_id": {"type": "string"}, "tool_name": {"type": "string"}, "arguments": {"type": "object"}, "confirm": {"type": "boolean"}}, required=["action"], risk="medium", permission="exec", description="Skill operations. find requires query; load/inspect require skill_name; MCP actions require provider_id and mcp_call also requires tool_name."),
    _entry("agent.manage", _handle_agent, {
        **_COMMON,
        "action": {"type": "string", "enum": ["spawn", "list", "get", "status", "cancel", "merge"]},
        "instruction": {"type": "string", "description": "Required for spawn: the complete task delegated to the subagent."},
        "profile_id": {"type": "string", "description": "Optional subagent profile; defaults to research_agent."},
        "max_turns": {"type": "integer", "minimum": 1, "maximum": 20},
        "background": {"type": "boolean"},
        "session_id": {"type": "string"},
        "subtask_id": {"type": "string"},
        "parent_task_id": {"type": "string"},
    }, required=["action"], description="Subagent task management. action=spawn requires instruction; get/cancel/merge use the subtask_id returned by spawn."),
    _entry("system.manage", _handle_system, {**_COMMON, **_SYSTEM_ARGS, "limit": {"type": "integer", "minimum": 1}, "action": {"type": "string", "enum": ["diagnostics", "health", "selfcheck", "local_info", "tasks", "audit_log", "run_get", "session_get", "session_checkpoint", "session_rewind", "session_export", "session_snapshot"]}}, required=["action"], risk="medium", description="Runtime health, current local date/time and host facts, durable tasks, audit logs, run details, and session operations. local_info returns timezone-aware current time plus host/IP/OS facts; run_get requires run_id; session actions require session_id; rewind additionally requires snapshot_id."),
    _entry("text.analyze", _handle_text, {**_COMMON, "action": {"type": "string", "enum": ["redact", "extract_entities", "match"]}, "text": {"type": "string"}, "pattern": {"type": "string"}}, required=["action"], description="Text redact, extract and match."),
    _entry("workspace.file", _handle_workspace_file, {**_COMMON, **_WORKSPACE_FILE_ARGS, "action": {"type": "string", "enum": ["list", "read", "read_image", "extract_document", "extract_document_image", "extract_document_images", "write", "write_artifact", "edit", "patch", "glob", "delete"]}}, required=["action"], risk="medium", description="Workspace files. extract_document reads a managed text, DOCX, PDF, XLSX, or PPTX attachment by file_id and reports embedded_image_count for DOCX. extract_document_image extracts one DOCX image by file_id and 1-based image_index. extract_document_images extracts an ordered DOCX image batch (up to 8) for visual analysis; its image evidence is automatically delivered to the next model turn. Never pass a returned file_id to read/read_image because those actions require a workspace filepath. write/write_artifact require filename and content.", execution_contract={
        "batching": [{
            "source_action": "extract_document_image",
            "target_action": "extract_document_images",
            "group_by": ["file_id"],
            "index_arg": "image_index",
            "start_arg": "start_index",
            "limit_arg": "limit",
            "max_batch_size": 8,
        }],
        "reference_kinds": {
            "extract_document": {"file_id": "managed_file"},
            "extract_document_image": {"file_id": "managed_file"},
            "extract_document_images": {"file_id": "managed_file"},
            "read": {"filepath": "workspace_path"},
            "read_image": {"filepath": "workspace_path"},
            "edit": {"filepath": "workspace_path"},
            "patch": {"filepath": "workspace_path"},
            "delete": {"filepath": "workspace_path"},
        },
    }),
    _entry("workspace.artifact", _handle_workspace_artifact, {**_COMMON, "action": {"type": "string", "enum": ["list", "read", "save", "tag", "delete"]}, "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1}, "artifact_id": {"type": "string"}, "content": {"type": "string"}, "title": {"type": "string"}, "status": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "artifact_type": {"type": "string"}}, required=["action"], description="Workspace artifact operations."),
    _entry("workspace.filestore", _handle_workspace_filestore, {**_COMMON, "action": {"type": "string", "enum": ["references", "import", "reconcile_trash_preview", "reconcile_trash"]}, "file_id": {"type": "string"}, "filepath": {"type": "string"}}, required=["action"], description="FileStore references, import, and hash-verified legacy trash reconciliation."),
    _entry("workspace.metadata.get", _handle_workspace_metadata, {"workspace_id": {"type": "string"}}, description="Workspace metadata."),
    _entry("workspace.document.pdf.extract_text", _handle_pdf_extract, {"workspace_id": {"type": "string"}, "filepath": {"type": "string"}, "page_range": {"type": "string"}}, required=["filepath"], description="Extract PDF text."),
]


CANONICAL_REGISTRY: dict[str, CanonicalToolEntry] = {
    entry.canonical_tool_id: entry for entry in _RAW_REGISTRY
}


def list_canonical_ids() -> list[str]:
    return sorted(CANONICAL_REGISTRY)


def get_entry(canonical_tool_id: str) -> CanonicalToolEntry:
    try:
        return CANONICAL_REGISTRY[canonical_tool_id]
    except KeyError as exc:
        raise KeyError(f"unknown canonical_tool_id: {canonical_tool_id}") from exc


def to_tool_specs() -> list[tuple[ToolSpec, Callable[[ToolInvocation], dict]]]:
    out: list[tuple[ToolSpec, Callable[[ToolInvocation], dict]]] = []
    for entry in CANONICAL_REGISTRY.values():
        from core.tools.tool_namespace import get_namespace_entry
        ns_entry = get_namespace_entry(entry.canonical_tool_id)
        spec = ToolSpec(
            tool_id=entry.canonical_tool_id,
            handler_id=entry.canonical_tool_id,
            name=ns_entry.display_name,
            description=ns_entry.usage_hint or entry.description,
            category=ns_entry.category,
            risk_level=entry.risk_level,
            input_schema=entry.input_schema,
            enabled=entry.enabled,
            requires_approval=entry.requires_approval,
            callable_by_llm=entry.callable_by_llm,
            permission_action=entry.permission_action,
            metadata={**ns_entry.metadata(), **(entry.execution_contract or {})},
        )
        out.append((spec, entry.handler))
    from extensions.runtime import get_extension_tool_specs
    extension_specs = get_extension_tool_specs()
    duplicate_ids = {spec.tool_id for spec, _ in extension_specs} & set(CANONICAL_REGISTRY)
    if duplicate_ids:
        raise ValueError(f"extension tools conflict with core tools: {sorted(duplicate_ids)}")
    out.extend(extension_specs)
    return out


def to_openai_tools() -> list[dict[str, Any]]:
    from agent.llm.tool_adapter import tool_spec_to_openai_function
    from core.tools.catalog_snapshot import build_action_profiles_for_tool
    from extensions.runtime import get_extension_tool_specs
    from core.tools.tool_namespace import get_namespace_entry
    out = []
    for tool_id, entry in CANONICAL_REGISTRY.items():
        ns_entry = get_namespace_entry(tool_id)
        description = ns_entry.usage_hint or entry.description
        if ns_entry.not_for:
            description = f"{description}\n\nAvoid: {ns_entry.not_for}"
        out.append(tool_spec_to_openai_function({
            "tool_id": tool_id,
            "description": description,
            "input_schema": entry.input_schema,
            "risk_level": entry.risk_level,
            "requires_approval": entry.requires_approval,
            "action_profiles": build_action_profiles_for_tool(
                tool_id,
                input_schema=entry.input_schema,
                category=ns_entry.category,
                base_permission=entry.permission_action or "read",
            ),
            "metadata": {**ns_entry.metadata(), **(entry.execution_contract or {})},
        }))
    for spec, _handler in get_extension_tool_specs():
        if not spec.callable_by_llm:
            continue
        out.append(tool_spec_to_openai_function({
            "tool_id": spec.tool_id,
            "description": spec.description or spec.name or spec.tool_id,
            "input_schema": spec.input_schema,
            "risk_level": spec.risk_level,
            "requires_approval": spec.requires_approval,
            "action_profiles": build_action_profiles_for_tool(
                spec.tool_id,
                input_schema=spec.input_schema,
                category=spec.category,
                base_permission=spec.permission_action,
            ),
            "metadata": spec.metadata,
        }))
    return out
