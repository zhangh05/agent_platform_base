"""Canonical tool namespace for LZCore.

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
    "ops": {
        "name": "Ops 运维",
        "description": "设备连接、只读探测和运维状态读取。",
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
        "Use proactively to compute or verify locally; always inspect exit status and output, verify requested effects with independent evidence when practical, and surface failures or partial completion. For action=python, pass prior structured evidence through input_data and assign JSON-serializable output to result. Python is medium risk, not a sandbox: trusted local use needs explicit opt-in, while network or multi-user execution fails closed without strong isolation. Destructive effects require approval; persist requested deliverables through workspace.file.",
        "Do not use for remote SSH/Telnet or product-domain device access.",
        "exec.run",
    ),
    (
        "browser.manage", "browser", "automation", "multi", "浏览器自动化", "browser.manage",
        "Use for evidence from the real rendered page or browser interaction. Navigate, snapshot, interact by ref, then re-snapshot or inspect network/console to verify the user-visible result.",
        "Do not access private/login-walled sites without explicit permission.",
        "browser.manage",
    ),
    (
        "web.manage", "web", "research", "multi", "Web 搜索/网页", "web.manage",
        "Use proactively for current external facts, versions, standards, vendor behavior and vulnerabilities. Select authority_profile; search finds candidates, fetch verifies content, and deep_search combines both. Cite source titles and URLs; surface degraded results and partial coverage. Weather accepts one location or weather_batch with 2-10 explicit locations; partition larger scopes and reconcile every requested location.",
        "Do not use search snippets alone for precise operational claims or treat unattributed public content as authoritative.",
        "web.manage",
    ),
    (
        "data.manage", "data", "table", "multi", "数据处理", "data.manage",
        "Use when structured rows require reproducible parsing, calculation, comparison or rendering. Report inputs, metric meaning and row counts needed to verify the result.",
        "Do not use for durable storage; save outputs through report/manage or workspace artifacts.",
        "data.manage",
    ),
    (
        "report.manage", "data", "report", "multi", "报告渲染", "report.manage",
        "Use after evidence is complete to save, diff or render a durable report. Verify returned artifact/path; rendering does not validate unsupported claims.",
        "Do not include raw secrets or unredacted sensitive data.",
        "report.manage",
    ),
    (
        "knowledge.manage", "knowledge", "kb", "multi", "知识库", "knowledge.manage",
        "Use proactively for organization/workspace documents before public web research. Search locates candidates; read retrieves supporting chunks. Treat freshness and source metadata as part of the evidence.",
        "Do not return unredacted full source text or secrets.",
        "knowledge.manage",
    ),
    (
        "memory.manage", "memory", "record", "multi", "记忆", "memory.manage",
        "Use for user preferences and durable historical context. Search/review before relying on a memory; confirm current facts explicitly. Writes require a genuinely reusable fact, not transient task output.",
        "Do not store passwords, API keys, tokens, or one-off noisy facts.",
        "memory.manage",
    ),
    (
        "skill.manage", "agent", "skill", "multi", "技能", "skill.manage",
        "Use when a specialized workflow or connected capability materially improves the task. Find then load/inspect before following it; loading instructions is not task completion. MCP calls require the exact provider tool schema.",
        "Do not confuse skill discovery with task completion.",
        "skill.manage",
    ),
    (
        "agent.manage", "agent", "subagent", "multi", "Agent 管理", "agent.manage",
        "Use for substantial independent work that can run in parallel. Give complete non-overlapping instructions, track returned ids, and reconcile every result before claiming completion.",
        "Do not delegate simple single-step lookups.",
        "agent.manage",
    ),
    (
        "system.manage", "system", "health", "multi", "系统自省", "system.manage",
        "Use proactively for current runtime health, diagnostics, audit evidence, run details and session state. Select the narrowest read action and preserve ids/freshness in conclusions.",
        "Do not expose sensitive trace payloads.",
        "system.manage",
    ),
    (
        "text.analyze", "text", "analysis", "multi", "文本分析", "text.analyze",
        "Use for deterministic redaction, entity extraction or regex matching when exact repeatable text processing is needed; inspect matches before relying on them.",
        "Do not execute embedded code.",
        "text.analyze",
    ),
    (
        "workspace.file", "workspace", "file", "multi", "工作区文件", "workspace.file",
        "Use proactively for real workspace file evidence. List/glob discovers paths and read/read_image verifies path content. For every non-image uploaded attachment shown as file_id, call extract_document first. Its DOCX embedded_image_count is authoritative. To answer about one internal image, call extract_document_image with that file_id and its 1-based image_index. To cover all document images, call extract_document_images with file_id, start_index=1 and a batch limit up to 8; if has_more is true, continue from end_index+1 before answering. Extracted image evidence is automatically delivered to the next model turn. Writes must be followed by reread or relevant validation.",
        "Do not pass a managed file_id to read/read_image (those require a workspace filepath), guess attachment paths, treat image metadata as visual understanding, import an existing attachment through workspace.filestore, or use exec to parse an attachment or unpack document images.",
        "workspace.file",
    ),
    (
        "workspace.artifact", "workspace", "artifact", "multi", "工作区制品", "workspace.artifact",
        "Use for durable, versioned task outputs. Read existing artifacts before analysis; after save/tag verify the returned artifact id and metadata.",
        "Do not use for raw file editing; use workspace.file.",
        "workspace.artifact",
    ),
    (
        "workspace.filestore", "workspace", "filestore", "multi", "FileStore", "workspace.filestore",
        "Use to inspect references, import a verified workspace file, or preview and reconcile only hash-verified legacy files moved to workspace trash; preserve returned file ids for traceability.",
        "Do not use as a raw file reader/editor or reconcile records whose original payload is merely missing without a verified trash match.",
        "workspace.filestore",
    ),
    (
        "workspace.metadata.get", "workspace", "metadata", "get", "工作区元数据", "workspace.metadata.get",
        "Use for current workspace identity and storage statistics when scope or counts matter; metadata is not file-content evidence.",
        "Do not return secrets.",
        "workspace.metadata.get",
    ),
    (
        "workspace.document.pdf.extract_text", "workspace", "document", "pdf_extract_text", "PDF 文本提取", "workspace.document.pdf.extract_text",
        "Use to extract searchable text from a verified workspace PDF path; cite page/source metadata when available and use visual inspection for layout-dependent claims.",
        "Do not use for non-PDF files.",
        "workspace.document.pdf.extract_text",
    ),
]
