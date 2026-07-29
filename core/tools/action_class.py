# core/tools/action_class.py
"""Action class inference for tools.

v2.3.2: Uses category/group/action to classify each tool into:
- read: list/read/get/search/summary/preview/exists/validate/diff
- write: save/write/edit/patch/tag/export/render
- mutate: create/update/delete/disable/reindex/import/load/unload/confirm/set/rewind/checkpoint
- execute: exec/run/slash_run/spawn_
- external: web/search/page/save_artifact/news/weather/extract_links
"""

from dataclasses import dataclass
from typing import Optional


# ─── Action Class Definitions ────────────────────────────────────────────

READ_ACTIONS = {
    "list", "read", "get", "search", "query", "summary", "preview",
    "exists", "validate", "diff", "status", "extract", "classify",
    "keywords.extract", "explain", "workspace.file.read",
}

WRITE_ACTIONS = {
    "save", "write", "edit", "patch", "tag", "export", "render",
    "profile.set", "artifact_save", "write_artifact",
}

MUTATE_ACTIONS = {
    "add", "create", "update", "delete", "disable", "reindex", "import",
    "load", "unload", "confirm", "set", "rewind", "checkpoint",
    "delete_soft", "archive.preview", "reindex_all", "restore",
    "snapshot.create", "snapshot.list",  # session snapshots are metadata writes
}

EXECUTE_ACTIONS = {
    "exec", "run", "slash_run",
}

EXTERNAL_ACTIONS = {
    "search", "page", "summarize", "news", "weather",
    "extract_links", "save_artifact",
}

# Category-based overrides: certain categories imply action classes
CATEGORY_EXECUTE = {"host"}
CATEGORY_EXTERNAL = {"web"}

# Tools that are safe even when in MUTATE/WRITE/EXEC classes
SAFE_EXECUTE = {"runtime.selfcheck", "system.diagnostics", "system.diagnostics"}
SAFE_WRITE = {"workspace.file"}  # not actually destructive
SAFE_MUTATE = {"skill.load"}  # LLM-visible capability activation, not business data mutation


# ─── Classification ──────────────────────────────────────────────────────

@dataclass
class ActionClass:
    tool_id: str
    category: str
    group: str
    action: str
    action_class: str = "read"    # read | write | mutate | execute | external
    is_destructive: bool = False
    is_high_impact: bool = False

    def __repr__(self):
        return f"{self.tool_id} → {self.action_class}"


def classify_tool(tool_id: str, category: str = "", group: str = "",
                  action: str = "", arguments: dict | None = None) -> ActionClass:
    """Classify a tool's action class from its category/group/action.

    v3.9.2: ``arguments`` enables sub-action dispatch for merged tools
    whose canonical ``action`` is ``"multi"``. The actual class is then
    looked up by ``arguments.action``. Non-merged tools pass through unchanged.
    """
    result = ActionClass(
        tool_id=tool_id, category=category, group=group, action=action
    )

    # v3.9.2: merged tools dispatch on arguments.action.
    # Non-multi tools keep their original single-action semantics.
    effective_action = action
    if action == "multi" and arguments:
        sub = str(arguments.get("action", "")).lower().strip()
        if sub:
            effective_action = sub

    # Category-based overrides
    if category in CATEGORY_EXECUTE:
        result.action_class = "execute"
    elif effective_action in EXECUTE_ACTIONS:
        result.action_class = "execute"
    elif effective_action in MUTATE_ACTIONS:
        result.action_class = "mutate"
    elif effective_action in WRITE_ACTIONS:
        result.action_class = "write"
    elif effective_action in EXTERNAL_ACTIONS and category in CATEGORY_EXTERNAL:
        # Only web-category "search/summarize" etc. are external
        result.action_class = "external"
    # Default: "read" for list/read/get/search/query etc.

    # Mark destructive/high-impact
    if result.action_class in ("mutate", "execute"):
        result.is_high_impact = True
    # v3.1.1: "spawn" creates read-only sub-agents — NOT destructive.
    # It passes through the approval gate for high-impact tools.
    if effective_action in ("add", "delete", "delete_soft", "disable", "unload", "rewind", "checkpoint",
                  "reindex_all", "restore"):
        result.is_destructive = True

    # Safety overrides
    if tool_id in SAFE_EXECUTE and result.action_class == "execute":
        result.action_class = "read"
        result.is_high_impact = False
    if tool_id in SAFE_WRITE and result.action_class == "write":
        result.action_class = "read"
    if tool_id in SAFE_MUTATE and result.action_class == "mutate":
        result.action_class = "read"
        result.is_high_impact = False
        result.is_destructive = False

    return result


def should_be_planner_visible(action_class: ActionClass, scene_context: dict = None) -> bool:
    """Determine if a tool should be planner_visible based on action class.

    Rules:
    - read: yes
    - write: only if scene explicitly needs write tools
    - execute (non-destructive, e.g. spawn): yes — approval gate handles risk
    - mutate / execute (destructive): only if scene allowlist explicitly permits
    - external: yes
    """
    if not action_class:
        return True

    if action_class.action_class in ("read", "external"):
        return True

    if action_class.action_class == "write":
        if scene_context and scene_context.get("allow_write"):
            return True
        return False

    # execute / mutate: allow non-destructive; block destructive unless allowlisted
    if action_class.action_class in ("execute", "mutate"):
        if not action_class.is_destructive:
            return True  # e.g. agent.manage(action=spawn) — approval gate handles risk
        # Destructive: only if scene allowlist explicitly permits
        allowed = (scene_context or {}).get("allowed_actions", set())
        return action_class.action in allowed

    return False
