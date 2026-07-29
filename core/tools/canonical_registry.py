"""Canonical tool registry for Agent Platform Base.

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
    return {"ok": True, "matches": matches, "count": len(matches)}


def _local_delete(inv: ToolInvocation) -> dict:
    from core.tools.general_tools.shared import _caller_workspace, _workspace_path

    args = inv.arguments or {}
    filepath = str(args.get("filepath") or args.get("path") or "").strip()
    if not filepath:
        return {"ok": False, "error": "filepath is required"}
    ws = _caller_workspace(inv)
    target = _workspace_path(ws, filepath)
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": "file not found"}
    trash = _workspace_path(ws, ".trash")
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / target.name
    i = 1
    while dest.exists():
        dest = trash / f"{target.stem}.{i}{target.suffix}"
        i += 1
    target.rename(dest)
    return {"ok": True, "deleted": True, "trash_path": str(dest.relative_to(_workspace_path(ws)))}


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
            return {"ok": False, "error": "Agent Platform Base supports local execution only"}
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
        return browser.browser_snapshot(compact=bool(args.get("compact", True)), max_elements=int(args.get("max_elements") or 50))
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
        search_result = _handle_web(ToolInvocation(tool_id=inv.tool_id, arguments={**args, "action": "search"}, workspace_id=inv.workspace_id))
        return {"ok": bool(search_result.get("ok", True)), "summary": "deep_search fallback returned search results; fetch selected URLs explicitly when needed.", "search": search_result}
    return _unsupported(inv, "search|fetch|weather|deep_search|list")


def _handle_data(inv: ToolInvocation) -> dict:
    from core.tools.general_tools import data_engine

    args = inv.arguments or {}
    action = _action(inv) or "parse"
    text = str(args.get("text") or "")
    rows = args.get("rows")
    if action == "parse":
        return data_engine.data_parse(text=text, rows=rows)
    if action == "stats":
        return data_engine.data_stats(text=text, rows=rows)
    if action == "distinct":
        return data_engine.data_distinct(text=text, rows=rows, column=str(args.get("column") or ""))
    if action == "aggregate":
        return data_engine.data_aggregate(text=text, rows=rows, group_by=args.get("group_by"), metrics=args.get("metrics"))
    if action == "filter":
        return data_engine.data_filter(text=text, rows=rows, conditions=args.get("conditions"), max_rows=int(args.get("max_rows") or 50))
    if action == "sort":
        return data_engine.data_sort(text=text, rows=rows, by=args.get("by"), order=str(args.get("order") or "asc"), max_rows=int(args.get("max_rows") or 50))
    if action == "render":
        return data_engine.data_render(text=text, rows=rows, output=str(args.get("output") or "markdown"), max_rows=int(args.get("max_rows") or 50))
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
        handle_knowledge_reindex,
        handle_knowledge_search,
    )

    action = _action(inv) or "search"
    level = str((inv.arguments or {}).get("level") or "chunk").lower()
    if action == "search":
        return handle_knowledge_search(inv)
    if action == "read":
        return handle_knowledge_get_source(inv) if level == "source" else handle_knowledge_get_chunk_summary(inv)
    if action == "import":
        return handle_knowledge_index_artifact(inv)
    if action == "manage":
        return handle_knowledge_reindex(inv)
    if action in {"list", "chunk"}:
        return handle_knowledge_search(inv)
    return _unsupported(inv, "search|read|list|chunk|import|manage")


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
    from core.tools.general_tools.skill_tools import handle_skill_find, handle_skill_inspect, handle_skill_list, handle_skill_load

    action = _action(inv) or "list"
    return {
        "list": handle_skill_list,
        "find": handle_skill_find,
        "search": handle_skill_find,
        "load": handle_skill_load,
        "inspect": handle_skill_inspect,
    }.get(action, lambda x: _unsupported(x, "list|search|load|inspect"))(inv)


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
    from core.tools.general_tools.runtime_tools import handle_runtime_diagnostics, handle_runtime_health, handle_runtime_local_info, handle_runtime_selfcheck
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
        "tasks": handle_runtime_diagnostics,
        "audit_log": handle_runtime_diagnostics,
        "run_get": handle_run_get_merged,
        "session_get": handle_session_get_merged,
        "session_checkpoint": handle_session_checkpoint,
        "session_rewind": handle_session_rewind,
        "session_export": handle_session_export,
        "session_snapshot": handle_session_snapshot,
        "review_list": handle_runtime_diagnostics,
        "review_update": handle_runtime_diagnostics,
    }.get(action, lambda x: _unsupported(x, "diagnostics|health|selfcheck|local_info|tasks|audit_log|run_get|session_get|session_checkpoint|session_rewind|session_export|session_snapshot|review_list|review_update"))(inv)


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
        return {"ok": True, "matches": matches[:100], "match_count": len(matches)}
    return _unsupported(inv, "redact|extract|extract_entities|match")


def _handle_workspace_file(inv: ToolInvocation) -> dict:
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
        "edit": handle_file_edit,
        "patch": handle_file_patch,
        "write": handle_ws_write_artifact_file,
        "write_artifact": handle_ws_write_artifact_file,
        "glob": _local_glob,
        "delete": _local_delete,
    }.get(action, lambda x: _unsupported(x, "list|read|read_image|edit|patch|write|write_artifact|glob|delete"))(inv)


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
    from core.tools.general_tools.filestore_tools import handle_file_import_workspace_path, handle_file_references

    action = _action(inv) or "references"
    if action == "references":
        return handle_file_references(inv, file_id=str((inv.arguments or {}).get("file_id") or ""))
    if action == "import":
        return handle_file_import_workspace_path(inv, filepath=str((inv.arguments or {}).get("filepath") or ""))
    return _unsupported(inv, "references|import")


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
) -> CanonicalToolEntry:
    return CanonicalToolEntry(
        canonical_tool_id=tool_id,
        handler=handler,
        input_schema=_schema(properties, required),
        risk_level=risk,
        permission_action=permission,
        description=description,
        handler_id=tool_id,
    )


_COMMON = {
    "workspace_id": {"type": "string"},
    "action": {"type": "string"},
    "query": {"type": "string"},
    "limit": {"type": "integer"},
    "filepath": {"type": "string"},
    "artifact_id": {"type": "string"},
    "content": {"type": "string"},
    "title": {"type": "string"},
}


_RAW_REGISTRY: list[CanonicalToolEntry] = [
    _entry("exec.run", _handle_exec, {
        **_COMMON,
        "action": {"type": "string", "enum": ["shell", "python", "slash"], "default": "shell"},
        "command": {"type": "string"},
        "code": {"type": "string"},
        "description": {"type": "string"},
        "target": {"type": "string", "enum": ["local"], "default": "local"},
        "shell": {"type": "string", "enum": ["cmd", "powershell"], "default": "cmd"},
    }, required=["action"], risk="medium", permission="exec", description="Local command execution."),
    _entry("browser.manage", _handle_browser, {**_COMMON, "action": {"type": "string", "enum": ["navigate", "snapshot", "screenshot", "click", "type", "extract", "scroll", "hover", "press_key", "select_option", "evaluate", "wait", "tabs", "network", "console", "navigate_back", "close"]}, "url": {"type": "string"}, "selector": {"type": "string"}, "ref": {"type": "string"}, "text": {"type": "string"}, "script": {"type": "string"}, "key": {"type": "string"}}, required=["action"], risk="medium", description="Browser automation."),
    _entry("web.manage", _handle_web, {**_COMMON, "action": {"type": "string", "enum": ["search", "fetch", "weather", "deep_search", "list"]}, "url": {"type": "string"}, "source": {"type": "string"}, "location": {"type": "string"}, "days": {"type": "integer", "description": "Forecast days for weather action."}}, required=["action"], description="Web search, fetch and weather."),
    _entry("data.manage", _handle_data, {**_COMMON, "action": {"type": "string", "enum": ["parse", "stats", "distinct", "aggregate", "filter", "sort", "render", "pivot", "join"]}, "text": {"type": "string"}, "rows": {"type": "array"}, "column": {"type": "string"}, "conditions": {"type": "array"}, "group_by": {"type": "array"}, "metrics": {"type": "array"}}, required=["action"], description="Structured data processing."),
    _entry("report.manage", _handle_report, {**_COMMON, "action": {"type": "string", "enum": ["save", "diff", "document"]}, "left": {"type": "string"}, "right": {"type": "string"}}, required=["action"], description="Report save, diff and document rendering."),
    _entry("knowledge.manage", _handle_knowledge, {**_COMMON, "action": {"type": "string", "enum": ["search", "read", "list", "chunk", "import", "manage"]}, "level": {"type": "string"}, "chunk_id": {"type": "string"}, "source_id": {"type": "string"}}, required=["action"], risk="medium", description="Knowledge search/read/import/manage."),
    _entry("memory.manage", _handle_memory, {**_COMMON, "action": {"type": "string", "enum": ["search", "review", "confirm", "create", "update", "delete", "profile_get", "profile_set"]}, "memory_id": {"type": "string"}, "scope": {"type": "string"}, "field": {"type": "string"}, "value": {"type": "string"}}, required=["action"], risk="medium", description="Memory search and management."),
    _entry("skill.manage", _handle_skill, {**_COMMON, "action": {"type": "string", "enum": ["list", "find", "load", "inspect"]}, "skill_name": {"type": "string"}}, required=["action"], description="Skill discovery."),
    _entry("agent.manage", _handle_agent, {**_COMMON, "action": {"type": "string", "enum": ["spawn", "list", "get", "status", "cancel", "merge"]}, "session_id": {"type": "string"}, "child_session_id": {"type": "string"}, "subtask_id": {"type": "string"}}, required=["action"], description="Subagent task management."),
    _entry("system.manage", _handle_system, {**_COMMON, "action": {"type": "string", "enum": ["diagnostics", "health", "selfcheck", "local_info", "tasks", "audit_log", "run_get", "session_get", "session_checkpoint", "session_rewind", "session_export", "session_snapshot", "review_list", "review_update"]}, "run_id": {"type": "string"}, "session_id": {"type": "string"}, "review_id": {"type": "string"}}, required=["action"], risk="medium", description="Runtime diagnostics and session operations."),
    _entry("text.analyze", _handle_text, {**_COMMON, "action": {"type": "string", "enum": ["redact", "extract_entities", "match"]}, "text": {"type": "string"}, "pattern": {"type": "string"}}, required=["action"], description="Text redact, extract and match."),
    _entry("workspace.file", _handle_workspace_file, {**_COMMON, "action": {"type": "string", "enum": ["list", "read", "read_image", "write", "write_artifact", "edit", "patch", "glob", "delete"]}, "subdir": {"type": "string"}, "pattern": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}, "patch_text": {"type": "string"}}, required=["action"], risk="medium", description="Workspace file operations."),
    _entry("workspace.artifact", _handle_workspace_artifact, {**_COMMON, "action": {"type": "string", "enum": ["list", "read", "save", "tag", "delete"]}, "status": {"type": "string"}, "tags": {"type": "array"}, "artifact_type": {"type": "string"}}, required=["action"], description="Workspace artifact operations."),
    _entry("workspace.filestore", _handle_workspace_filestore, {**_COMMON, "action": {"type": "string", "enum": ["references", "import"]}, "file_id": {"type": "string"}}, required=["action"], description="FileStore references and import."),
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
            metadata=ns_entry.metadata(),
        )
        out.append((spec, entry.handler))
    return out


def to_openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool_id.replace(".", "__"),
                "description": entry.description,
                "parameters": entry.input_schema,
            },
        }
        for tool_id, entry in CANONICAL_REGISTRY.items()
    ]
