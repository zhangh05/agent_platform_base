# context/schema_registry.py
"""Schema whitelist registry for ContextItem types.

Each item_type maps to a set of allowed field names. The compressor
uses this to strip fields by whitelist rather than by blacklist,
eliminating the class of bugs where legitimate fields (e.g. "content")
are accidentally redacted.

v3.1.0: Created as part of P1-P5 refactoring.
"""

from __future__ import annotations

# -----------------------------------------------------------------
# Core fields present on ALL ContextItem types
# -----------------------------------------------------------------
_COMMON_FIELDS = frozenset({
    "item_id",
    "item_type",
    "workspace_id",
    "created_at",
    "updated_at",
    "deleted",
    "deleted_at",
    "source",
    "priority",
    "title",
    "summary",
    "content",
    "sensitivity",
    "scope",
    "token_estimate",
    "citation_id",
    "source_id",
    "redaction_applied",
    "trace_id",
    "run_id",
    "tags",
    # metadata is handled specially (recursively filtered)
})

# -----------------------------------------------------------------
# Per-type extended fields (union with _COMMON_FIELDS)
# -----------------------------------------------------------------
_TYPE_EXTENSIONS: dict[str, frozenset[str]] = {
    "memory_hit": frozenset({
        "memory_id",
        "memory_type",
        "score",
        "relevance",
        "confidence",
        "tags",
        "workspace_id",
        "expires_at",
    }),
    "knowledge_chunk": frozenset({
        "chunk_id",
        "parent_chunk_id",
        "chunk_type",
        "chapter",
        "section",
        "subsection",
        "page_start",
        "page_end",
        "chunk_index",
        "index_text",
        "token_count",
        "score",
        "relevance",
        "source_type",
        "tags",
        "author",
        "language",
    }),
    "profile": frozenset({
        "profile_id",
        "profile_field",
        "value",
        "confidence",
    }),
    "artifact_ref": frozenset({
        "artifact_id",
        "artifact_type",
        "path",
        "workspace_id",
        "run_id",
    }),
    "report_section": frozenset({
        "report_id",
        "section_index",
        "section_title",
    }),
    "job_event": frozenset({
        "job_id",
        "event_type",
        "event_at",
        "run_id",
    }),
    "workspace_state": frozenset({
        "workspace_id",
        "last_run_id",
        "runs_count",
        "memory_count",
        "current_files",
    }),
}

# -----------------------------------------------------------------
# Metadata sub-fields that are ALWAYS stripped (structural secrets)
# -----------------------------------------------------------------
METADATA_BLOCKED_KEYS = frozenset({
    "absolute_path",
    "raw_prompt",
    "file_content",
    "report_content",
    "source_config",
    "generated_output",
    "api_key",
    "api_secret",
    "password",
    "token",
    "secret",
    "credential",
    "private_key",
    "auth_header",
})


# -----------------------------------------------------------------
# Public API
# -----------------------------------------------------------------

def allowed_fields(item_type: str) -> frozenset[str]:
    """Return the set of allowed top-level fields for *item_type*.

    Unknown types get only _COMMON_FIELDS (safe default).
    """
    ext = _TYPE_EXTENSIONS.get(item_type, frozenset())
    return _COMMON_FIELDS | ext


def is_metadata_key_blocked(key: str) -> bool:
    """Return True if *key* is a structural secret that must be redacted."""
    kl = key.lower()
    if kl in METADATA_BLOCKED_KEYS:
        return True
    # Heuristic: keys containing "path" are usually filesystem paths
    if "path" in kl and kl not in ("xpath", "json_path", "jsonpath"):
        return True
    return False


def strip_by_schema(item: dict) -> dict:
    """Return a copy of *item* with only whitelisted fields kept.

    - Top-level fields not in the whitelist are dropped.
    - ``metadata`` is recursively filtered against METADATA_BLOCKED_KEYS.
    - Unknown item_type gets only common fields (fail-safe).
    """
    item_type = item.get("item_type", "request")
    allowed = allowed_fields(item_type)

    out: dict = {}
    for k, v in item.items():
        if k == "metadata":
            out["metadata"] = _filter_metadata(v) if isinstance(v, dict) else {}
            continue
        if k in allowed:
            out[k] = v
    return out


def _filter_metadata(meta: dict) -> dict:
    """Recursively remove blocked keys from metadata dict."""
    out: dict = {}
    for k, v in meta.items():
        if is_metadata_key_blocked(k):
            continue
        if isinstance(v, dict):
            out[k] = _filter_metadata(v)
        else:
            out[k] = v
    return out
