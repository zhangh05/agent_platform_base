"""Provider-neutral prompt assembly fingerprints and cache usage semantics.

The runtime owns *what* is stable or dynamic.  Provider adapters only decide
how their wire protocol expresses caching.  No provider-specific field is
allowed to change prompt meaning.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import re
from typing import Any, Iterable

from agent.llm.schemas import LLMMessage, LLMRequest
from agent.runtime.token_tracker import estimate_text


PROMPT_ASSEMBLY_VERSION = "lzcore.prompt.v2"
_SUBAGENT_MARKER = "\n\n## Subagent assignment\n"
_TAG_NAMES = (
    "runtime_identity",
    "conversation_history",
    "governed_context",
    "current_user_request",
)
_GUIDANCE_SOURCES = (
    "runtime_clock",
    "managed_attachment",
    "workbench_skill",
    "task_continuation",
    "task_state",
    "operational_guard",
    "capability_playbook",
    "cognitive_state",
)


def split_stable_system(content: str) -> tuple[str, str]:
    """Split cache-stable kernel instructions from per-call system guidance."""
    text = str(content or "")
    if _SUBAGENT_MARKER not in text:
        return text, ""
    stable, dynamic = text.split(_SUBAGENT_MARKER, 1)
    return stable, "## Subagent assignment\n" + dynamic


def build_prompt_profile(req: LLMRequest, cfg: dict[str, Any]) -> dict[str, Any]:
    """Describe the rendered prompt without exposing its content.

    Fingerprints make cache fragmentation observable while layer sizes explain
    which class of context changed.  They are hashes only; prompts, tool
    arguments, device data and user text never enter diagnostics.
    """
    stable_system: list[str] = []
    dynamic_system: list[str] = []
    non_system: list[LLMMessage] = []
    for message in req.messages:
        if message.role == "system":
            stable, dynamic = split_stable_system(_text_content(message.content))
            if stable:
                stable_system.append(stable)
            if dynamic:
                dynamic_system.append(dynamic)
        else:
            non_system.append(message)

    tool_json = _canonical_tools(req.tools or [])
    stable_text = "\n\n".join(stable_system)
    dynamic_system_text = "\n\n".join(dynamic_system)
    message_text = "\n".join(_message_text(message) for message in non_system)
    tagged = _tagged_layer_sizes(message_text)
    guidance = _guidance_layer_sizes(message_text)
    skill_text = guidance["workbench_skill"]["_text"]
    stable_prefix_material = json.dumps(
        {
            "version": PROMPT_ASSEMBLY_VERSION,
            "system": stable_text,
            "tools": tool_json,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    all_material = stable_prefix_material + "\n" + dynamic_system_text + "\n" + message_text
    stable_fingerprint = _fingerprint(stable_prefix_material)
    tool_fingerprint = _fingerprint(tool_json)
    cache_shard = _cache_shard(message_text)
    layers = {
        "stable_system": _layer(stable_text, cacheable=True),
        "tool_schemas": _layer(tool_json, cacheable=True),
        "dynamic_system": _layer(dynamic_system_text, cacheable=False),
        "runtime_identity": tagged["runtime_identity"],
        "conversation_history": tagged["conversation_history"],
        "governed_context": tagged["governed_context"],
        "selected_skill": _layer(skill_text, cacheable=False),
        "runtime_clock": _public_layer(guidance["runtime_clock"]),
        "managed_attachments": _public_layer(guidance["managed_attachment"]),
        "capability_playbooks": _public_layer(guidance["capability_playbook"]),
        "operational_guards": _public_layer(guidance["operational_guard"]),
        "task_state": _public_layer(guidance["task_state"]),
        "cognitive_state": _public_layer(guidance["cognitive_state"]),
        "current_request": tagged["current_user_request"],
        "continuation_messages": _layer(message_text, cacheable=False),
    }
    provider_type = str(cfg.get("provider_type") or "openai_compatible")
    provider = str(cfg.get("provider") or cfg.get("default_provider") or "custom")
    return {
        "version": PROMPT_ASSEMBLY_VERSION,
        "provider": provider,
        "provider_type": provider_type,
        "model": str(cfg.get("model") or req.model or ""),
        "strategy": cache_strategy(cfg),
        "stable_prefix_fingerprint": stable_fingerprint,
        "tool_surface_fingerprint": tool_fingerprint,
        "assembly_fingerprint": _fingerprint(all_material),
        "cache_key": f"{PROMPT_ASSEMBLY_VERSION}:{stable_fingerprint[:24]}:s{cache_shard}",
        "cache_shard": cache_shard,
        "layers": layers,
        "stable_prefix_estimated_tokens": (
            layers["stable_system"]["estimated_tokens"]
            + layers["tool_schemas"]["estimated_tokens"]
        ),
        "selected_skill": bool(skill_text),
    }


def cache_strategy(cfg: dict[str, Any]) -> str:
    """Return the protocol strategy without claiming unsupported capabilities."""
    if not prompt_cache_enabled(cfg):
        return "disabled"
    provider_type = str(cfg.get("provider_type") or "openai_compatible")
    provider = str(cfg.get("provider") or cfg.get("default_provider") or "custom")
    if provider_type == "anthropic_messages":
        return "anthropic_explicit"
    if provider == "openai":
        return "openai_automatic"
    if provider_type in {"openai_compatible", "ollama_compatible"}:
        return "compatible_prefix_only"
    return "unsupported"


def prompt_cache_enabled(cfg: dict[str, Any]) -> bool:
    import os

    disabled_values = {"0", "false", "no", "off", "disabled"}
    global_raw = os.environ.get("LZCORE_PROMPT_CACHE_ENABLED")
    if global_raw is not None and str(global_raw).strip().lower() in disabled_values:
        return False
    provider_raw = cfg.get("prompt_cache_enabled", True)
    if isinstance(provider_raw, bool):
        return provider_raw
    return str(provider_raw).strip().lower() not in disabled_values


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize Anthropic and OpenAI cache counters without double counting."""
    if not isinstance(usage, dict):
        return usage
    result = dict(usage)
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}

    cache_read = _int(
        usage.get("cache_read_input_tokens", details.get("cached_tokens", 0))
    )
    cache_creation = _int(
        usage.get("cache_creation_input_tokens", details.get("cache_write_tokens", 0))
    )
    output = _int(usage.get("output_tokens", usage.get("completion_tokens", 0)))
    reported_input = _int(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
    is_anthropic = "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage
    logical_input = (
        reported_input + cache_read + cache_creation if is_anthropic else reported_input
    )
    result.update({
        "logical_input_tokens": logical_input,
        "uncached_input_tokens": max(0, logical_input - cache_read - cache_creation),
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
        "normalized_output_tokens": output,
        "cache_hit_ratio": round(cache_read / max(logical_input, 1), 4),
    })
    return result


def stable_and_dynamic_system(messages: Iterable[LLMMessage]) -> tuple[str, str]:
    stable_parts: list[str] = []
    dynamic_parts: list[str] = []
    for message in messages:
        if message.role != "system":
            continue
        stable, dynamic = split_stable_system(_text_content(message.content))
        if stable:
            stable_parts.append(stable)
        if dynamic:
            dynamic_parts.append(dynamic)
    return "\n\n".join(stable_parts), "\n\n".join(dynamic_parts)


def _tagged_layer_sizes(text: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for tag in _TAG_NAMES:
        match = re.search(rf"<{tag}(?:\s[^>]*)?>[\s\S]*?</{tag}>", text)
        result[tag] = _layer(match.group(0) if match else "", cacheable=False)
    return result


def _guidance_layer_sizes(text: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for source in _GUIDANCE_SOURCES:
        blocks = "\n".join(
            match.group(0)
            for match in re.finditer(
                rf'<runtime_guidance\b(?=[^>]*\btrusted="true")(?=[^>]*\bsource_kind="{re.escape(source)}")[^>]*>[\s\S]*?</runtime_guidance>',
                text,
            )
        )
        result[source] = {**_layer(blocks, cacheable=False), "_text": blocks}
    return result


def _public_layer(layer: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in layer.items() if key != "_text"}


def _layer(text: str, *, cacheable: bool) -> dict[str, Any]:
    value = str(text or "")
    return {
        "characters": len(value),
        "estimated_tokens": estimate_text(value) if value else 0,
        "fingerprint": _fingerprint(value) if value else "",
        "cacheable": cacheable,
        "present": bool(value),
    }


def _message_text(message: LLMMessage) -> str:
    return f"{message.role}:{_text_content(message.content)}:{_canonical(message.tool_calls or [])}"


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return _canonical(content)


@lru_cache(maxsize=64)
def _canonical_tools_from_json(raw: str) -> str:
    try:
        return _canonical(json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw


def _canonical_tools(tools: list[dict[str, Any]]) -> str:
    raw = json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _canonical_tools_from_json(raw)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _cache_shard(message_text: str) -> int:
    """Bound cache routing hotspots without exposing workspace/session ids."""
    import os

    try:
        shard_count = max(1, min(256, int(os.environ.get("LZCORE_PROMPT_CACHE_SHARDS", "16"))))
    except (TypeError, ValueError):
        shard_count = 16
    identity = re.search(
        r"<runtime_identity(?:\s[^>]*)?>[\s\S]*?</runtime_identity>", message_text
    )
    seed = identity.group(0) if identity else "shared"
    return int(_fingerprint(seed)[:8], 16) % shard_count


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
