# agent/runtime/context_compactor.py
"""v2.0 Phase 2 → v3.3 Long-task optimization: Deterministic context compaction.

Strategy:
...

v3.3: PRESERVE_KEYS expanded for long-task data retention.
"""
from __future__ import annotations

import enum
import json
import time
from typing import Any

# ── Forbidden keys in any compacted context ──
FORBIDDEN_KEYS = {
    "secret", "password", "token", "api_key", "private_key",
    "source_config", "raw_config", "ssh_key", "credentials",
}

# ── v3.3: Keys to always preserve in compacted tool results ──
_PRESERVE_KEYS = {
    "ok", "summary", "tool_id", "artifacts",
    "source_count", "manual_review_count",
    "errors", "warnings",
    "output", "result", "devices", "hosts", "assets",
    "version", "model", "status", "region",
    "host", "port", "protocol", "device_type",
    "task_id", "workflow_id", "step_id", "progress",
}


def _is_cjk(c: str) -> bool:
    cp = ord(c)
    return (
        0x4E00 <= cp <= 0x9FFF or    # CJK Unified Ideographs
        0x3400 <= cp <= 0x4DBF or    # CJK Extension A
        0x3000 <= cp <= 0x303F or    # CJK Symbols and Punctuation
        0x31C0 <= cp <= 0x31EF or    # CJK Strokes
        0x3200 <= cp <= 0x32FF or    # Enclosed CJK Letters
        0x3300 <= cp <= 0x33FF or    # CJK Compatibility
        0xFE30 <= cp <= 0xFE4F or    # CJK Compatibility Forms
        0xFF00 <= cp <= 0xFFEF       # Halfwidth and Fullwidth Forms
    )


def _estimate_tokens(text: str) -> int:
    """CJK-aware token estimation: CJK ~1 char/token, ASCII ~4 chars/token."""
    if not text:
        return 0
    s = str(text)
    cjk = sum(1 for c in s if _is_cjk(c))
    non_cjk = len(s) - cjk
    return max(1, cjk + non_cjk // 4)


class CompactionStrategy(str, enum.Enum):
    """Strategy used to compact history.

    - fast_eviction: replace old messages with deterministic summaries
                     (no LLM call, ~0ms, used by the context pipeline).
    - llm_summary:   call LLM to summarize older messages into a coherent
                     paragraph (slower, higher quality, opt-in).
    """
    FAST_EVICTION = "fast_eviction"
    LLM_SUMMARY = "llm_summary"


class CompactionMetric:
    """Structured metric record for a single compaction event.

    Designed to be JSON-serializable for logs and frontend dashboards.
    """
    __slots__ = (
        "strategy",
        "trigger",            # "auto" | "manual"
        "threshold_pct",      # e.g. 75.0
        "original_messages",
        "original_estimated_tokens",
        "compacted_messages",
        "compacted_estimated_tokens",
        "compacted_message_count",
        "duration_ms",
        "reference_context_item_id",
        "ts",
    )

    def __init__(
        self,
        strategy: CompactionStrategy,
        trigger: str,
        threshold_pct: float,
        original_messages: int,
        original_estimated_tokens: int,
        compacted_messages: int,
        compacted_estimated_tokens: int,
        compacted_message_count: int,
        duration_ms: int,
        reference_context_item_id: str = "",
        ts: str = "",
    ) -> None:
        self.strategy = strategy
        self.trigger = trigger
        self.threshold_pct = threshold_pct
        self.original_messages = original_messages
        self.original_estimated_tokens = original_estimated_tokens
        self.compacted_messages = compacted_messages
        self.compacted_estimated_tokens = compacted_estimated_tokens
        self.compacted_message_count = compacted_message_count
        self.duration_ms = int(round(duration_ms))
        self.reference_context_item_id = reference_context_item_id
        self.ts = ts

    @property
    def retention_ratio(self) -> float:
        if self.original_estimated_tokens <= 0:
            return 0.0
        return round(self.compacted_estimated_tokens / self.original_estimated_tokens, 3)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy.value,
            "trigger": self.trigger,
            "threshold_pct": self.threshold_pct,
            "original_messages": self.original_messages,
            "original_estimated_tokens": self.original_estimated_tokens,
            "compacted_messages": self.compacted_messages,
            "compacted_estimated_tokens": self.compacted_estimated_tokens,
            "compacted_message_count": self.compacted_message_count,
            "duration_ms": self.duration_ms,
            "reference_context_item_id": self.reference_context_item_id,
            "retention_ratio": self.retention_ratio,
            "ts": self.ts,
        }


def estimate_context_size(messages: list) -> int:
    """Estimate token count for a list of messages (CJK-aware)."""
    total = 0
    for msg in (messages or []):
        content = ""
        if isinstance(msg, str):
            content = msg
        elif isinstance(msg, dict):
            content = str(msg.get("content", ""))
        elif hasattr(msg, "content"):
            content = str(getattr(msg, "content", ""))
        total += _estimate_tokens(content)
    return max(1, total)


def compact_tool_result_content(content: str, max_chars: int = 4000) -> str:
    """Compact a tool result JSON string: keep safe keys, strip forbidden keys.

    v3.10: the previous version always tried ``json.loads`` first
    but then fell back to ``_strip_forbidden`` (a line-based regex
    sweep) when the content was not JSON. That path silently
    mangled single-line JSON inputs by replacing the entire line
    with ``[REDACTED]`` — destroying the JSON envelope. The new
    flow picks the right strategy explicitly:

      * parse JSON when possible → redact object keys recursively
      * fall back to text scrubbing only when the input is NOT
        valid JSON

    Result: a single-line ``{"username": "abc", "password":
    "secret"}`` round-trips through ``json.loads`` after
    compaction.
    """
    if not content:
        return content

    # Try JSON first when the input looks structured.
    parsed_json: Any = _try_parse_json(content)
    if parsed_json is _JSON_PARSE_FAILED:
        # Non-JSON text — old line-based scrub is the right tool.
        if len(content) > max_chars:
            return _strip_forbidden(content[:max_chars])
        return _strip_forbidden(content)

    # JSON path. Redact recursively and keep the structure intact.
    redacted = _redact_json_object(parsed_json)
    text = json.dumps(redacted, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    # Truncate per-character but try to keep the JSON parseable.
    truncated = _truncate_json_preserving_structure(redacted, max_chars)
    return json.dumps(truncated, ensure_ascii=False, default=str)


_JSON_PARSE_FAILED = object()


def _try_parse_json(content: Any) -> Any:
    """Parse JSON only when the input looks like a JSON document.

    Returns the parsed value or the sentinel ``_JSON_PARSE_FAILED``
    when parsing fails. Returning the sentinel lets callers
    distinguish "not JSON" from "JSON that happens to be empty".
    """
    if not isinstance(content, str):
        return content
    stripped = content.strip()
    if not stripped:
        return _JSON_PARSE_FAILED
    if stripped[0] not in ("{", "["):
        return _JSON_PARSE_FAILED
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return _JSON_PARSE_FAILED


def _redact_json_object(node: Any) -> Any:
    """Recursively walk a parsed JSON node and redact forbidden keys.

    Strings are left intact (already serialised safe values); only
    forbidden KEYS are rewritten. ``_PRESERVE_KEYS`` overrides the
    default redact-on-match rule for keys the user explicitly
    whitelisted (e.g. ``host``, ``hostname``, ``subnet_mask``).
    """
    if isinstance(node, dict):
        out: dict = {}
        for k, v in node.items():
            if k in _PRESERVE_KEYS:
                out[k] = _redact_json_object(v)
            elif k in FORBIDDEN_KEYS:
                out[k] = "[REDACTED]"
            elif isinstance(k, str) and _is_forbidden_substring_key(k):
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact_json_object(v)
        return out
    if isinstance(node, list):
        return [_redact_json_object(item) for item in node]
    return node


def _is_forbidden_substring_key(key: str) -> bool:
    """Substring match on the key name (in addition to the exact
    ``FORBIDDEN_KEYS`` set). Whitelisted substrings (``host``,
    ``hostname``) are explicitly excluded so a ``hostname`` field
    does not get caught by the ``host`` substring rule."""
    lowered = key.lower()
    if "host" in lowered and "hostname" in lowered:
        # 'hostname' / 'host_name' / etc — explicitly safe.
        return False
    return any(forbidden in lowered for forbidden in FORBIDDEN_KEYS)


def _truncate_json_preserving_structure(obj: Any, max_chars: int) -> Any:
    """Truncate a parsed JSON object while keeping the result
    parseable. If the full dump fits, return as-is. Otherwise,
    shrink scalar values to fit, drop deeper levels first, and
    leave a ``_truncated: True`` marker on the dict root."""
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return obj
    if not isinstance(obj, dict):
        # Strings / lists — drop tail to fit.
        if isinstance(obj, str):
            return obj[:max_chars] + "…"
        if isinstance(obj, list):
            return obj[:5] + ["…(truncated)"] if obj else []
        return obj

    # Build a shrinking projection that always includes the marker
    # and as many keys as can fit.
    out: dict = {"_truncated": True, "_original_size": len(text)}
    # Try adding keys in declaration order; stop when next key would
    # push us past the budget.
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        projected = {**out, k: _truncate_json_preserving_structure(v, max_chars // 2)}
        candidate = json.dumps(projected, ensure_ascii=False, default=str)
        if len(candidate) > max_chars:
            break
        out[k] = projected[k]
    return out


def _strip_forbidden(text: str) -> str:
    """Remove lines containing forbidden *key* patterns (not substring matches)."""
    if not text:
        return text
    import re
    lines = text.split("\n")
    kept = []
    for line in lines:
        low = line.lower()
        # Only redact lines where forbidden key appears as a field name (k=v or "k":)
        if any(re.search(rf'\b{fk}\s*[=:]', low) for fk in FORBIDDEN_KEYS):
            kept.append("[REDACTED]")
        else:
            kept.append(line)
    return "\n".join(kept)


def _is_assistant_message(msg) -> bool:
    if isinstance(msg, dict):
        return msg.get("role") == "assistant"
    return hasattr(msg, "role") and getattr(msg, "role", "") == "assistant"


def _is_system_message(msg) -> bool:
    """Check if a message is a system message."""
    if isinstance(msg, dict):
        return msg.get("role") == "system"
    return hasattr(msg, "role") and getattr(msg, "role", "") == "system"


def _is_user_message(msg) -> bool:
    if isinstance(msg, dict):
        return msg.get("role") == "user"
    return hasattr(msg, "role") and getattr(msg, "role", "") == "user"


def _message_content(msg) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        return str(msg.get("content", ""))
    if hasattr(msg, "content"):
        return str(getattr(msg, "content", ""))
    return str(msg)


def _get_tool_calls(msg) -> list | None:
    """Extract tool_calls from a message if present."""
    if isinstance(msg, dict):
        return msg.get("tool_calls")
    return getattr(msg, "tool_calls", None)


def _get_tool_call_id(msg) -> str | None:
    """Extract tool_call_id from a tool result message."""
    if isinstance(msg, dict):
        return msg.get("tool_call_id")
    return getattr(msg, "tool_call_id", None)


def _protect_tool_pairs(messages, protected_indices: set) -> set:
    """Expand protected_indices so tool call ↔ tool result pairs stay together.

    In OpenAI format, an assistant message with ``tool_calls`` is followed by
    one or more ``tool`` result messages (``tool_call_id`` matching the call id).
    Breaking this pair causes the LLM to see orphaned tool results without
    knowing what was called, or tool calls without their results — both trigger
    hallucination. This helper ensures both sides of every call are either
    kept or compacted together.

    Logic:
    - If either side of a pair is protected (e.g. by recent cutoff or user/system),
      keep the other side too.
    - If neither side is protected, compact both (pair stays together)."""
    call_map: dict[str, int] = {}
    result_map: dict[str, int] = {}
    for i, m in enumerate(messages):
        tc = _get_tool_calls(m)
        if tc and _is_assistant_message(m):
            for c in tc:
                cid = c.get("id") if isinstance(c, dict) else getattr(c, "id", None)
                if cid:
                    call_map[cid] = i
        rid = _get_tool_call_id(m)
        if rid:
            result_map[rid] = i

    expanded = set(protected_indices)
    changed = True
    while changed:
        changed = False
        for cid, aidx in call_map.items():
            ridx = result_map.get(cid)
            if ridx is None:
                continue
            a_in = aidx in expanded
            r_in = ridx in expanded
            if a_in and not r_in:
                expanded.add(ridx)
                changed = True
            elif r_in and not a_in:
                expanded.add(aidx)
                changed = True
    return expanded


def compact_messages(
    messages: list,
    keep_recent: int = 15,
    strategy: CompactionStrategy = CompactionStrategy.FAST_EVICTION,
    trigger: str = "auto",
    threshold_pct: float = 75.0,
) -> tuple[list, dict]:
    """Compact message list.

    Returns (compacted_messages, metadata). metadata includes:
      - strategy: CompactionStrategy used
      - trigger: "auto" | "manual"
      - threshold_pct: trigger threshold at the time of compaction
      - compacted_message_count: how many messages were replaced
      - original_estimated_tokens / compacted_estimated_tokens
      - reference_context_item_id: id of the first kept message (context anchor)
      - duration_ms: time spent compacting
      - retention_ratio: compacted/original token ratio

    Rules:
    - Keeps system messages intact
    - Keeps most recent keep_recent messages intact
    - Replaces older non-system messages with deterministic summaries
    - Never compacts the last user message
    """
    from datetime import datetime, timezone

    t0 = time.perf_counter()
    if not messages or len(messages) <= keep_recent:
        return messages, {
            "compacted": False,
            "reason": "below_threshold",
            "strategy": strategy.value,
            "duration_ms": int(round((time.perf_counter() - t0) * 1000)),
        }

    # Find the user message position (must not be compacted)
    user_positions = [
        i for i, m in enumerate(messages)
        if _is_user_message(m)
    ]
    # Protect the last user message
    protected_indices = set()
    if user_positions:
        protected_indices.add(user_positions[-1])

    # Protect system messages
    for i, m in enumerate(messages):
        if _is_system_message(m):
            protected_indices.add(i)

    # Protect recent messages (before pair protection so pairs at boundary stay together)
    recent_start = max(0, len(messages) - keep_recent)
    for i in range(recent_start, len(messages)):
        protected_indices.add(i)

    # Protect tool call ↔ tool result pairs (must come after recent cutoff)
    protected_indices = _protect_tool_pairs(messages, protected_indices)

    original_est = estimate_context_size(messages)

    if strategy == CompactionStrategy.LLM_SUMMARY:
        protected_messages = [m for i, m in enumerate(messages) if i in protected_indices]
        old_messages = [m for i, m in enumerate(messages) if i not in protected_indices]
        summary = _build_progress_summary(old_messages)
        compacted = [summary, *protected_messages] if summary else protected_messages
        new_est = estimate_context_size(compacted)
        duration_ms = int(round((time.perf_counter() - t0) * 1000))
        return compacted, {
            "compacted": True,
            "strategy": strategy.value,
            "trigger": trigger,
            "threshold_pct": threshold_pct,
            "compacted_message_count": len(old_messages),
            "original_estimated_tokens": original_est,
            "compacted_estimated_tokens": new_est,
            "duration_ms": duration_ms,
            "reference_context_item_id": _first_reference_id(compacted),
            "retention_ratio": round(new_est / original_est, 3) if original_est > 0 else 0.0,
            "summary_message_created": bool(summary),
            "summary_source": "deterministic_fallback",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    compacted = []
    compacted_count = 0

    for i, msg in enumerate(messages):
        if i in protected_indices:
            compacted.append(msg)
        else:
            # Replace with deterministic summary
            summary = _build_deterministic_summary(msg)
            compacted.append(summary)
            compacted_count += 1

    new_est = estimate_context_size(compacted)
    duration_ms = int(round((time.perf_counter() - t0) * 1000))

    return compacted, {
        "compacted": True,
        "strategy": strategy.value,
        "trigger": trigger,
        "threshold_pct": threshold_pct,
        "compacted_message_count": compacted_count,
        "original_estimated_tokens": original_est,
        "compacted_estimated_tokens": new_est,
        "duration_ms": duration_ms,
        "reference_context_item_id": _first_reference_id(compacted),
        "retention_ratio": round(new_est / original_est, 3) if original_est > 0 else 0.0,
        "summary_message_created": False,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def build_compaction_metric(
    meta: dict,
    strategy: CompactionStrategy,
    trigger: str,
    threshold_pct: float,
    original_messages: int,
    reference_context_item_id: str = "",
) -> CompactionMetric:
    """Build a CompactionMetric from compact_messages() metadata."""
    from datetime import datetime, timezone
    return CompactionMetric(
        strategy=strategy,
        trigger=trigger,
        threshold_pct=threshold_pct,
        original_messages=original_messages,
        original_estimated_tokens=int(meta.get("original_estimated_tokens", 0)),
        compacted_messages=original_messages - int(meta.get("compacted_message_count", 0)),
        compacted_estimated_tokens=int(meta.get("compacted_estimated_tokens", 0)),
        compacted_message_count=int(meta.get("compacted_message_count", 0)),
        duration_ms=int(meta.get("duration_ms", 0)),
        reference_context_item_id=meta.get("reference_context_item_id") or reference_context_item_id,
        ts=meta.get("ts", "") or datetime.now(timezone.utc).isoformat(),
    )


def _build_deterministic_summary(msg) -> dict:
    """Build a short deterministic summary of a message."""
    role = "unknown"
    content = ""
    if isinstance(msg, dict):
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))
    elif hasattr(msg, "role"):
        role = getattr(msg, "role", "unknown")
        content = str(getattr(msg, "content", ""))

    # Build a short summary
    preview = _safe_summary_text(content, limit=150)

    if role == "tool":
        # Tool result: try to parse the JSON
        summary = _summarize_tool_content(content)
        return {
            "role": "tool",
            "content": f"[compacted tool result] {summary}",
        }
    elif role == "assistant":
        snippet = preview[:100].replace("\n", " ")
        return {
            "role": "assistant",
            "content": f"[compacted assistant turn: {snippet}]",
        }
    elif role == "user":
        snippet = preview[:100].replace("\n", " ")
        return {
            "role": "user",
            "content": f"[compacted user: {snippet}]",
        }
    return {"role": role, "content": f"[compacted {role} message]"}


def _build_progress_summary(messages: list) -> dict:
    """Build a compact progress summary for older history."""
    snippets = []
    tool_count = 0
    user_count = 0
    assistant_count = 0
    for msg in messages or []:
        role = "unknown"
        if isinstance(msg, dict):
            role = str(msg.get("role", "unknown"))
        elif hasattr(msg, "role"):
            role = str(getattr(msg, "role", "unknown"))
        content = _safe_summary_text(_message_content(msg), limit=220)
        if not content:
            continue
        if role == "tool":
            tool_count += 1
        elif role == "user":
            user_count += 1
        elif role == "assistant":
            assistant_count += 1
        if len(snippets) < 8:
            snippets.append(f"- {role}: {content[:180]}")
    content = (
        "[State of progress]\n"
        f"Compacted earlier context: {len(messages or [])} messages "
        f"({user_count} user, {assistant_count} assistant, {tool_count} tool).\n"
    )
    if snippets:
        content += "Key preserved points:\n" + "\n".join(snippets)
    return {
        "role": "assistant",
        "content": content[:3000],
        "message_id": "context_summary_auto",
        "metadata": {"compaction": "llm_summary", "source": "deterministic_fallback"},
    }


def _safe_summary_text(content: Any, *, limit: int) -> str:
    """Return a short compaction snippet without secret-bearing lines."""
    text = str(content or "").replace("\x00", "")
    scrubbed = _strip_forbidden(text)
    return scrubbed.replace("\n", " ").strip()[: max(1, limit)]


def _first_reference_id(messages: list) -> str:
    """Return first non-system context item id."""
    for m in messages or []:
        if isinstance(m, dict) and not _is_system_message(m):
            mid = m.get("message_id") or m.get("id") or m.get("run_id") or ""
            if mid:
                return str(mid)
        elif hasattr(m, "message_id"):
            mid = str(getattr(m, "message_id", "") or "")
            if mid:
                return mid
    return ""


def _summarize_tool_content(content: str) -> str:
    """Create a brief summary of a tool result."""
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            ok = data.get("ok", None)
            summary = str(data.get("summary", ""))[:100]
            return f"ok={ok} {summary}"
    except (json.JSONDecodeError, TypeError):
        pass
    return content[:80]


def should_compact(messages: list, max_context_tokens: int = 512000,
                   threshold: float = 0.75) -> bool:
    """Check if compaction is needed."""
    est = estimate_context_size(messages)
    return est > max_context_tokens * threshold


def compact_tool_result_payload(payload: dict, max_chars: int = 4000) -> dict:
    """Compact a single tool result payload for safe transmission.

    Always keeps: ok, summary, tool_id, artifacts, source_count,
    manual_review_count, errors, warnings.
    Strips forbidden keys.
    """
    safe = {
        k: payload.get(k) for k in (
            "ok", "summary", "tool_id",
            "source_count", "manual_review_count",
            "errors", "warnings",
        )
        if k in payload
    }
    if "artifacts" in payload:
        arts = payload["artifacts"]
        if isinstance(arts, list):
            safe["artifacts"] = [
                {"artifact_id": a.get("artifact_id", "")} if isinstance(a, dict) else str(a)
                for a in arts[:3]
            ]

    # Copy remaining safe keys up to 10 total
    for k, v in payload.items():
        if k in safe:
            continue
        if k in FORBIDDEN_KEYS:
            safe[k] = "[REDACTED]"
        elif len(safe) < 10:
            sv = str(v)
            if len(sv) > max_chars // 4:
                sv = sv[:max_chars // 4 - 3] + "..."
            safe[k] = sv

    return safe
