"""Task-aware model routing with an explicit, inspectable fallback policy."""

from __future__ import annotations

import os

from agent.llm.provider_store import PROVIDER_PRESETS
from agent.llm.settings import resolve_provider_llm_config


def _configured_provider(task: str) -> str:
    key = "LZCORE_MODEL_ROUTE_" + "".join(ch if ch.isalnum() else "_" for ch in task.upper())
    return os.environ.get(key, "").strip()


def resolve_model_candidates(task: str, active_config: dict) -> list[dict]:
    """Return the selected provider config and routing metadata.

    No route override means the existing active-provider behavior is preserved.
    Invalid overrides are ignored rather than causing a request outage.
    """
    requested = _configured_provider(task)
    if requested not in PROVIDER_PRESETS:
        return [{**active_config, "routing": {"task": task, "selected_by": "active_provider"}}]
    selected = resolve_provider_llm_config(requested)
    candidates = [{**selected, "routing": {"task": task, "selected_by": "task_policy"}}]
    if active_config.get("provider") and active_config.get("provider") != selected.get("provider"):
        candidates.append({**active_config, "routing": {"task": task, "selected_by": "active_fallback"}})
    return candidates


def resolve_model_route(task: str, active_config: dict) -> dict:
    """Compatibility helper returning the first selected candidate."""
    candidates = resolve_model_candidates(task, active_config)
    selected = dict(candidates[0])
    selected["routing"] = {**selected.get("routing", {}), "fallbacks": [item.get("provider", "") for item in candidates[1:]]}
    return selected
