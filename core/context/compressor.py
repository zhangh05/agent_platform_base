# context/compressor.py
"""Context compressor — limits, strips content, enforces budget, deduplicates.

v3.1.0 refactoring:
  - Schema-driven stripping via schema_registry (replaces blacklist).
  - Dynamic budget: adjusts max_chars based on LLM model's context window.
  - Semantic dedup: merges items with similar summaries to reduce redundancy.
"""

import json
import re
from difflib import SequenceMatcher
from core.context.schemas import ContextItem, ContextBudget, resolve_budget_for_model
from core.context.schema_registry import strip_by_schema, is_metadata_key_blocked


# Dedup similarity threshold (0-1): items with summary similarity above this are merged
DEDUP_SIMILARITY_THRESHOLD = 0.75


def compress_context_items(items: list, budget: ContextBudget = None,
                           mode: str = "safe_llm", model: str = "") -> tuple:
    """Redact sensitive fields while preserving every context item.

    Args:
        items: List of ContextItem objects.
        budget: Optional budget override. If None, resolved from model.
        mode: Compression mode ("safe_llm").
        model: LLM model name, used for dynamic budget resolution.

    Returns:
        (compressed_items, budget, warnings) tuple.
    """
    # Resolve budget dynamically if not provided
    if budget is None:
        budget = resolve_budget_for_model(model)

    warnings = []

    # A context budget is accounting, never a right to omit model evidence.
    compressed = []
    for item in items:
        # Strip sensitive keys inside the content payload.  Do not apply the
        # top-level ContextItem whitelist to ``content`` itself: artifact_id,
        # job_id, status and similar business fields are the whole point of
        # context and must survive compression.
        item.content = _strip_sensitive(item.content)
        compressed.append(item)

    # Compute usage for telemetry without removing content.
    total_chars = sum(len(json.dumps(i.content, ensure_ascii=False)) + len(i.summary) for i in compressed)
    budget.used_items = len(compressed)
    budget.used_chars = total_chars

    return compressed, budget, warnings


def _limit_for(item_type: str, budget: ContextBudget) -> int:
    m = {"memory_hit": budget.max_memory_hits, "artifact_summary": budget.max_artifact_refs,
         "job_summary": budget.max_job_events, "report_summary": budget.max_report_sections,
         "knowledge_chunk": budget.max_knowledge_chunks}
    return m.get(item_type, 50)


def _strip_sensitive(obj, item_type: str = ""):
    """Strip sensitive keys from a nested dict/list using schema_registry.

    v3.1.0: Replaced blacklist with schema_registry whitelist.
    - For top-level item dicts with known item_type, uses strip_by_schema()
      to keep only whitelisted fields.
    - For nested dicts (metadata, content sub-objects), only strips keys
      that are structural secrets (via is_metadata_key_blocked).
    - Legitimate data fields (content, summary, title) are NEVER stripped.
    """
    if isinstance(obj, dict):
        # If we have item_type context, use full schema filtering
        if item_type:
            obj_with_type = dict(obj)
            obj_with_type.setdefault("item_type", item_type)
            return strip_by_schema(obj_with_type)

        # For generic dicts (no item_type context), only strip blocked metadata keys
        out = {}
        for k, v in obj.items():
            if is_metadata_key_blocked(k):
                out[k] = "[redacted]"
                continue
            out[k] = _strip_sensitive(v)
        return out
    if isinstance(obj, list):
        return [_strip_sensitive(i) for i in obj]
    return obj


def _dedup_items(items: list) -> tuple:
    """Remove items with highly similar summaries.

    For each pair of items with the same item_type, if their summaries
    have similarity above DEDUP_SIMILARITY_THRESHOLD, keep only the one
    with higher priority.

    Complexity: O(n²) — capped at 30 items by budget.max_items.

    Returns:
        (deduped_items, removed_count)
    """
    if len(items) <= 1:
        return items, 0

    kept = []
    removed = 0

    for item in items:
        is_dup = False
        for existing in kept:
            # Only dedup within same item_type
            if existing.item_type != item.item_type:
                continue
            # Check summary similarity
            if existing.summary and item.summary:
                ratio = SequenceMatcher(
                    None, existing.summary.lower(), item.summary.lower()
                ).ratio()
                if ratio > DEDUP_SIMILARITY_THRESHOLD:
                    # Lower numeric priority means higher business priority
                    # (P0 request, P1 explicit ref, ...).  Keep the more
                    # authoritative item instead of letting a later low-value
                    # duplicate crowd it out.
                    if item.priority < existing.priority:
                        kept.remove(existing)
                        kept.append(item)
                    is_dup = True
                    removed += 1
                    break
        if not is_dup:
            kept.append(item)

    return kept, removed
