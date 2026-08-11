"""Action-level argument requirements for merged canonical tools.

Flat function schemas expose every supported argument to the model. This table
adds the conditional requirements that JSON Schema ``required`` cannot express
for a merged ``tool + action`` interface. SemanticValidator consumes it before
execution, so missing identifiers fail as planning errors rather than opaque
handler errors.
"""

from __future__ import annotations


ACTION_REQUIRED_ALL: dict[tuple[str, str], tuple[str, ...]] = {
    ("exec.run", "shell"): ("command",),
    ("exec.run", "slash"): ("command",),
    ("exec.run", "python"): ("code",),
    ("browser.manage", "navigate"): ("url",),
    ("browser.manage", "type"): ("text",),
    ("browser.manage", "extract"): ("url",),
    ("browser.manage", "press_key"): ("key",),
    ("browser.manage", "select_option"): ("value",),
    ("browser.manage", "evaluate"): ("script",),
    ("web.manage", "search"): ("query",),
    ("web.manage", "deep_search"): ("query",),
    ("web.manage", "fetch"): ("url",),
    ("web.manage", "weather"): ("location",),
    ("data.manage", "distinct"): ("column",),
    ("data.manage", "filter"): ("conditions",),
    ("data.manage", "sort"): ("by",),
    # ``values`` is only meaningful for sum/avg.  Count pivots intentionally
    # operate without it, so the handler validates it after inspecting aggfunc.
    ("data.manage", "pivot"): ("index", "columns"),
    ("data.manage", "join"): ("on",),
    ("report.manage", "save"): ("content",),
    ("report.manage", "diff"): ("text_a", "text_b"),
    ("report.manage", "document"): ("summary",),
    ("knowledge.manage", "search"): ("query",),
    ("knowledge.manage", "import"): ("artifact_id",),
    ("knowledge.manage", "reindex"): ("source_id",),
    ("memory.manage", "create"): ("content",),
    ("memory.manage", "update"): ("memory_id",),
    ("memory.manage", "confirm"): ("memory_id",),
    ("memory.manage", "delete"): ("memory_id",),
    ("memory.manage", "profile_set"): ("field", "value"),
    ("skill.manage", "find"): ("query",),
    ("skill.manage", "load"): ("skill_name",),
    ("skill.manage", "inspect"): ("skill_name",),
    ("skill.manage", "mcp_list_tools"): ("provider_id",),
    ("skill.manage", "mcp_call"): ("provider_id", "tool_name"),
    ("agent.manage", "spawn"): ("instruction",),
    ("agent.manage", "cancel"): ("subtask_id",),
    ("agent.manage", "merge"): ("parent_task_id",),
    ("system.manage", "run_get"): ("run_id",),
    ("system.manage", "session_get"): ("session_id",),
    ("system.manage", "session_checkpoint"): ("session_id",),
    ("system.manage", "session_rewind"): ("session_id", "snapshot_id"),
    ("system.manage", "session_export"): ("session_id",),
    ("system.manage", "session_snapshot"): ("session_id",),
    ("text.analyze", "redact"): ("text",),
    ("text.analyze", "extract_entities"): ("text",),
    ("text.analyze", "match"): ("text", "pattern"),
    ("workspace.file", "read"): ("filepath",),
    ("workspace.file", "read_image"): ("filepath",),
    ("workspace.file", "extract_document"): ("file_id",),
    ("workspace.file", "extract_document_image"): ("file_id", "image_index"),
    ("workspace.file", "extract_document_images"): ("file_id",),
    ("workspace.file", "write"): ("filename", "content"),
    ("workspace.file", "write_artifact"): ("filename", "content"),
    ("workspace.file", "edit"): ("filepath", "old_string", "new_string"),
    ("workspace.file", "patch"): ("filepath", "patch_text"),
    ("workspace.file", "delete"): ("filepath",),
    ("workspace.artifact", "read"): ("artifact_id",),
    ("workspace.artifact", "save"): ("content",),
    ("workspace.artifact", "tag"): ("artifact_id", "tags"),
    ("workspace.artifact", "delete"): ("artifact_id",),
    ("workspace.filestore", "references"): ("file_id",),
    ("workspace.filestore", "import"): ("filepath",),
}


ACTION_REQUIRED_ANY: dict[tuple[str, str], tuple[tuple[str, ...], ...]] = {
    ("browser.manage", "click"): (("selector", "ref"),),
    ("browser.manage", "type"): (("selector", "ref"),),
    ("browser.manage", "hover"): (("selector", "ref"),),
    ("browser.manage", "select_option"): (("selector", "ref"),),
    ("knowledge.manage", "read"): (("chunk_id", "source_id"),),
    ("agent.manage", "get"): (("subtask_id",),),
    ("agent.manage", "merge"): (("subtask_id",),),
}


DATA_INPUT_ACTIONS = frozenset({
    "parse", "stats", "distinct", "aggregate", "filter", "sort", "render", "pivot", "join",
})
for _action in DATA_INPUT_ACTIONS:
    ACTION_REQUIRED_ANY.setdefault(("data.manage", _action), tuple())
    ACTION_REQUIRED_ANY[("data.manage", _action)] += (("text", "rows"),)
ACTION_REQUIRED_ANY[("data.manage", "join")] += (("right_text", "right_rows"),)
