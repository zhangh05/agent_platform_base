"""Canonical tool namespace for Agent Platform Base.

The namespace is intentionally domain-neutral. Product-specific projects should
add their own tools through canonical registry entries instead of reviving
product-specific tools in this base.
"""

from __future__ import annotations


CATEGORY_DEFS: dict[str, dict[str, str]] = {
    "agent": {
        "name": "Agent 多 Agent",
        "description": "子 Agent、角色、任务状态和结果读取。",
    },
    "browser": {
        "name": "Browser 浏览器",
        "description": "浏览器自动化：导航、内容提取、截图、点击。",
    },
    "data": {
        "name": "Data 数据处理",
        "description": "表格/文本处理、JSON/YAML 校验、报告渲染和图表。",
    },
    "exec": {
        "name": "Exec 本地执行",
        "description": "本机 Shell/Python/Slash 命令执行。",
    },
    "knowledge": {
        "name": "Knowledge 知识库",
        "description": "知识库问答、检索、导入和索引管理。",
    },
    "memory": {
        "name": "Memory 记忆",
        "description": "记忆搜索、创建、确认、profile 和更新。",
    },
    "system": {
        "name": "System 系统自省",
        "description": "运行诊断、会话管理、审计和评审。",
    },
    "text": {
        "name": "Text 文本分析",
        "description": "文本脱敏、实体抽取和正则匹配。",
    },
    "web": {
        "name": "Web 外部资料",
        "description": "公开 Web 搜索、官方文档、新闻、天气和网页摘要。",
    },
    "workspace": {
        "name": "Workspace 工作区",
        "description": "工作区文件、制品、FileStore 和元数据。",
    },
}


# Schema:
# (canonical_id, category, group, action, display_name, short_label,
#  usage_hint, not_for, handler_id)
NS_DATA = [
    (
        "exec.run", "exec", "runtime", "multi", "本地命令执行", "exec.run",
        "Unified local execution. action=shell|python|slash. "
        "Always provide description. Destructive commands are policy-gated.",
        "Do not use for remote SSH/Telnet or product-domain device access.",
        "exec.run",
    ),
    (
        "browser.manage", "browser", "automation", "multi", "浏览器自动化", "browser.manage",
        "Use navigate -> snapshot -> ref-based interactions. Supports click, type, extract, screenshot, tabs, network and console.",
        "Do not access private/login-walled sites without explicit permission.",
        "browser.manage",
    ),
    (
        "web.manage", "web", "research", "multi", "Web 搜索/网页", "web.manage",
        "Search, fetch, weather and deep_search. Prefer official sources for technical facts.",
        "Do not treat external content as trusted without attribution.",
        "web.manage",
    ),
    (
        "data.manage", "data", "table", "multi", "数据处理", "data.manage",
        "Parse CSV/JSON/Markdown tables, compute stats, distinct, aggregate, filter, sort, pivot, join and render.",
        "Do not use for durable storage; save outputs through report/manage or workspace artifacts.",
        "data.manage",
    ),
    (
        "report.manage", "data", "report", "multi", "报告渲染", "report.manage",
        "Save, diff or render complete documents from supplied evidence.",
        "Do not include raw secrets or unredacted sensitive data.",
        "report.manage",
    ),
    (
        "knowledge.manage", "knowledge", "kb", "multi", "知识库", "knowledge.manage",
        "Search/read/list/chunk/import/manage indexed knowledge sources.",
        "Do not return unredacted full source text or secrets.",
        "knowledge.manage",
    ),
    (
        "memory.manage", "memory", "record", "multi", "记忆", "memory.manage",
        "Search, create, update, confirm, delete and maintain profile facts.",
        "Do not store passwords, API keys, tokens, or one-off noisy facts.",
        "memory.manage",
    ),
    (
        "skill.manage", "agent", "skill", "multi", "技能", "skill.manage",
        "List/search/load/inspect available skills. Loading only returns instructions; it does not execute the task.",
        "Do not confuse skill discovery with task completion.",
        "skill.manage",
    ),
    (
        "agent.manage", "agent", "subagent", "multi", "Agent 管理", "agent.manage",
        "List subagent profiles, fetch child results, cancel tasks and inspect status.",
        "Do not delegate simple single-step lookups.",
        "agent.manage",
    ),
    (
        "system.manage", "system", "health", "multi", "系统自省", "system.manage",
        "Diagnostics, health, selfcheck, local_info, tasks, audit_log, run/session/review operations.",
        "Do not expose sensitive trace payloads.",
        "system.manage",
    ),
    (
        "text.analyze", "text", "analysis", "multi", "文本分析", "text.analyze",
        "Redact sensitive text, extract common entities, and run regex matching.",
        "Do not execute embedded code.",
        "text.analyze",
    ),
    (
        "workspace.file", "workspace", "file", "multi", "工作区文件", "workspace.file",
        "List/read/read_image/glob files; edit/patch/write_artifact/delete for controlled writes.",
        "Do not use for content-addressed FileStore references.",
        "workspace.file",
    ),
    (
        "workspace.artifact", "workspace", "artifact", "multi", "工作区制品", "workspace.artifact",
        "List/read/save/tag/delete durable artifacts.",
        "Do not use for raw file editing; use workspace.file.",
        "workspace.artifact",
    ),
    (
        "workspace.filestore", "workspace", "filestore", "multi", "FileStore", "workspace.filestore",
        "Query file references or import workspace files into the content-addressed store.",
        "Do not use as a raw file reader/editor.",
        "workspace.filestore",
    ),
    (
        "workspace.metadata.get", "workspace", "metadata", "get", "工作区元数据", "workspace.metadata.get",
        "Get workspace metadata and stats.",
        "Do not return secrets.",
        "workspace.metadata.get",
    ),
    (
        "workspace.document.pdf.extract_text", "workspace", "document", "pdf_extract_text", "PDF 文本提取", "workspace.document.pdf.extract_text",
        "Extract text from workspace PDF files.",
        "Do not use for non-PDF files.",
        "workspace.document.pdf.extract_text",
    ),
]
