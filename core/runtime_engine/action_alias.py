"""
Canonical action alias normalization for SSOT Runtime — single source of truth.

Both QueryLoop normalization and the PreExecutionRepairEngine
resolve model-side action aliases through the
single ``resolve_action_alias()`` entry point defined here. Adding a
new alias = one entry in ``CANONICAL_ALIASES_BY_TOOL``.

The resolution contract:

  resolve_action_alias(tool_id, action) -> AliasResolution
    .matched             — True if action was rewritten from a known alias
    .original_action     — the action string the caller passed in
    .canonical_action    — the rewritten token (== original when no rewrite)
    .operation           — secondary "operation" hint, e.g. "get_history" for
                            ``session_get``. Always None when the alias is
                            a pure synonym.
    .source              — "canonical" | "none"
                            "canonical" — resolved through this module
                            "none"      — no rewrite; original is left alone

Drift discipline:

  * All *stable* aliases (LLM terminology we have observed in
    production and want to keep supporting for the foreseeable
    future) MUST live in ``CANONICAL_ALIASES_BY_TOOL`` /
    ``CANONICAL_ALIASES_GLOBAL``.
  * Unknown actions are not silently rewritten. QueryLoop returns the
    validation error to the model for a schema-correct retry.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Final


# ── Resolution result ──────────────────────────────────────────────────

# Allowed values for ``AliasResolution.source``.
SOURCE_CANONICAL: Final[str] = "canonical"
SOURCE_NONE: Final[str] = "none"

VALID_SOURCES: Final[frozenset[str]] = frozenset(
    {SOURCE_CANONICAL, SOURCE_NONE}
)


@dataclass
class AliasResolution:
    """The single result type returned by ``resolve_action_alias()``."""

    matched: bool = False
    original_action: str = ""
    canonical_action: str = ""
    operation: str | None = None
    source: str = SOURCE_NONE
    notes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ── Canonical alias tables ─────────────────────────────────────────────
# Single source of truth. QueryLoop and PreExecutionRepairEngine both
# consult this table before semantic validation rejects an action.

# Per-tool alias map. Values are ``(canonical_action, operation)`` —
# ``operation`` is propagated into ``node.args["operation"]`` when
# present so the downstream tool receives the intent hint.
CANONICAL_ALIASES_BY_TOOL: Final[dict[str, dict[str, tuple[str, str | None]]]] = {
    # system.manage — keep the alias surface flat so the planner can
    # emit the colloquial form without us mutating the canonical enum
    # declared in ``contracts.py``.
    "system.manage": {
        # session — current canonical action is session_get. Keep
        # colloquial aliases pointing at that token; do not rewrite
        # canonical session_get back to the removed "session" action.
        "get_session": ("session_get", "get_history"),
        "session_history": ("session_get", "get_history"),
        "history_get": ("session_get", "get_history"),
        "session_list": ("session_get", "list"),
        "list_sessions": ("session_get", "list"),

        # review / audit / tasks
        "review_get": ("review_list", "get"),
        "audit_get": ("audit_log", "get"),
        "get": ("tasks", "get"),
        "tasks_get": ("tasks", "get"),

        # run history
        "get_run": ("run_get", "get"),
        "run_list": ("run_get", "list"),
        "list_runs": ("run_get", "list"),

        # diagnostics / health / selfcheck (no operation hint — pure
        # synonyms)
        "self_check": ("selfcheck", None),
        "check_health": ("health", None),
        "do_diagnostics": ("diagnostics", None),
        "diag": ("diagnostics", None),
        "local_ip": ("local_info", None),
        "local_info": ("local_info", None),
        "host_info": ("local_info", None),
        "system_info": ("local_info", None),
    },

    "workspace.file": {
        "ls": ("list", None),
        "cat": ("read", None),
    },

    "knowledge.manage": {
        "knowledge_get": ("read", None),
        "knowledge_search": ("search", None),
        "knowledge_read": ("read", None),
        "read_knowledge": ("read", None),
        "knowledge_import": ("import", None),
        "import_knowledge": ("import", None),
        "find_knowledge": ("search", None),
        "search_knowledge": ("search", None),
    },

    "agent.manage": {
        "agent_list": ("list", None),
    },

    "memory.manage": {
        "memory_search": ("search", None),
        "search_memory": ("search", None),
        "memory_create": ("create", None),
        "create_memory": ("create", None),
        "memory_delete": ("delete", None),
        "delete_memory": ("delete", None),
    },

    "web.manage": {
        "search_web": ("search", None),
        "web_search": ("search", None),
        "fetch_page": ("fetch", None),
        "page": ("fetch", None),
        "deep_web_search": ("deep_search", None),
    },

    "report.manage": {
        "render_report": ("document", None),
        "generate_report": ("document", None),
        "save_report": ("save", None),
    },

}

# Tool-agnostic aliases — apply regardless of which tool the planner
# was emitting. Use sparingly: a per-tool entry is almost always
# preferable.
CANONICAL_ALIASES_GLOBAL: Final[dict[str, tuple[str, str | None]]] = {}


# Canonical enum sets per tool — mirrors the canonical registry action enums.
# Keep this table aligned with ``core.tools.canonical_registry`` because the
# pre-execution repair path consults it before schema validation.
_CANONICAL_ACTIONS: Final[dict[str, frozenset[str]]] = {
    "exec.run": frozenset({
        "shell", "python", "slash", "background", "stream",
    }),
    "system.manage": frozenset({
        "diagnostics", "health", "selfcheck", "local_info", "tasks", "audit_log",
        "run_get", "session_get", "session_checkpoint", "session_rewind",
        "session_export", "session_snapshot", "review_list", "review_update",
    }),
    "workspace.file": frozenset({
        "list", "read", "read_image", "edit", "patch",
        "write_artifact", "glob", "delete",
    }),
    "knowledge.manage": frozenset({
        "search", "read", "list", "chunk", "import", "manage",
    }),
    "agent.manage": frozenset({
        "list", "get", "cancel", "status",
    }),
    "browser.manage": frozenset({
        "navigate", "snapshot", "screenshot", "click", "type", "extract",
        "scroll", "hover", "press_key", "select_option", "evaluate", "wait",
        "fill_form", "tabs", "network", "console", "navigate_back", "close",
    }),
    "web.manage": frozenset({"search", "fetch", "weather", "deep_search", "list"}),
    "data.manage": frozenset({
        "parse", "stats", "distinct", "aggregate", "filter",
        "sort", "render", "pivot", "join",
    }),
    "report.manage": frozenset({
        "save", "diff", "document",
    }),
    "text.analyze": frozenset({
        "redact", "extract", "match",
    }),
    "memory.manage": frozenset({
        "search", "create", "update", "confirm", "delete",
        "review", "profile_get", "profile_set",
    }),
    "skill.manage": frozenset({
        "list", "search", "load", "inspect",
    }),
    "workspace.artifact": frozenset({
        "list", "read", "save", "tag", "delete",
    }),
    "workspace.filestore": frozenset({
        "references", "import",
    }),
}


# ── Unified resolution entry point ─────────────────────────────────────

def resolve_action_alias(
    tool_id: str, action: str | None
) -> AliasResolution:
    """Resolve ``action`` for ``tool_id`` against the canonical table.

    Behavior contract:

      * unknown / empty action → ``matched=False, source="none"``
      * action is an alias in either per-tool or global table
        → ``matched=True, source="canonical"`` with
        ``canonical_action`` rewritten and ``operation`` propagated
      * action is already a member of the tool's canonical enum
        → ``matched=False, source="canonical"`` (the caller treats
        this as "no rewrite needed")

    This function never raises. It is the single entry point used by
    QueryLoop and PreExecutionRepairEngine; no other code path may
    implement its own alias lookup.
    """
    original = (action or "").strip() if isinstance(action, str) else ""
    if not original:
        return AliasResolution(
            matched=False,
            original_action=original,
            canonical_action="",
            operation=None,
            source=SOURCE_NONE,
        )

    # 1. Already canonical — no rewrite needed. This check intentionally
    # runs before alias lookup because a token can be both a historical alias
    # and the current canonical action after a hard-cut rename.
    if original in _CANONICAL_ACTIONS.get(tool_id, frozenset()):
        return AliasResolution(
            matched=False,
            original_action=original,
            canonical_action=original,
            operation=None,
            source=SOURCE_CANONICAL,
        )

    # 2. Per-tool table.
    by_tool = CANONICAL_ALIASES_BY_TOOL.get(tool_id) or {}
    if original in by_tool:
        canonical, op = by_tool[original]
        return AliasResolution(
            matched=True,
            original_action=original,
            canonical_action=canonical,
            operation=op,
            source=SOURCE_CANONICAL,
        )

    # 3. Tool-agnostic table.
    if original in CANONICAL_ALIASES_GLOBAL:
        canonical, op = CANONICAL_ALIASES_GLOBAL[original]
        return AliasResolution(
            matched=True,
            original_action=original,
            canonical_action=canonical,
            operation=op,
            source=SOURCE_CANONICAL,
        )

    # 4. Not found anywhere — let the semantic validator reject it.
    return AliasResolution(
        matched=False,
        original_action=original,
        canonical_action=original,
        operation=None,
        source=SOURCE_NONE,
    )
