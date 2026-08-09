"""
QueryLoop — iterative LLM + tool execution engine.

The single tool-capable runtime loop owns reasoning, execution, and response,
feeds tool results back for iterative refinement, tracks long tasks,
records retry metadata, and auto-compacts long conversations.

Optimizations:
  1. Prompt Cache — static system+tools prefix never changes
  2. One runtime contract — reasoning and user response share one system prompt
  3. Iterative execution — tool results feed back for dynamic decisions
  4. Streaming tool exec — tools start during LLM output
  5. Auto-compact — summarise old turns when context grows
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import re
import time
from dataclasses import asdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .models import (
    ExecutionNode,
    ExecutionStatus,
    SSOTRuntimeConfig,
    StatelessContext,
    ToolResult,
)
from .tracking import extract_tracking_payload, normalize_tracking_payload
from .context_budget import (
    RuntimeContextBudget,
    estimate_json_tokens,
    estimate_text_tokens,
    truncate_text_to_tokens,
)
from agent.llm.schemas import LLMMessage, LLMResponse, LLMToolCall
from agent.llm.tool_adapter import tool_spec_to_openai_function
from .prompt_contract import (
    RUNTIME_SYSTEM_PROMPT,
    build_runtime_system_prompt,
    build_turn_message,
)


# ── Prompt Cache ────────────────────────────────────────────────────────────

# Static prefix that never changes between turns — cached by the LLM API.
# Keep this concise: the full tool catalog is already supplied through the
# function-calling tools field on every planner call.
QUERY_LOOP_SYSTEM_PROMPT = RUNTIME_SYSTEM_PROMPT
RESPONSE_ONLY_MARKER = "[RESPONSE_ONLY]"


_TOOL_DEFINITION_CACHE: dict[str, List[dict]] = {}


def _tool_meta_get(meta: Any, key: str, default: Any = None) -> Any:
    if isinstance(meta, dict):
        return meta.get(key, default)
    return getattr(meta, key, default)


def _tool_registry_signature(tool_registry: dict) -> str:
    """Stable hash for the LLM-visible tool surface."""
    payload = []
    for tool_id, meta in sorted(tool_registry.items()):
        payload.append({
            "tool_id": tool_id,
            "description": _tool_meta_get(meta, "description", ""),
            "args_schema": _tool_meta_get(meta, "args_schema", _tool_meta_get(meta, "input_schema", {})),
            "risk_level": _tool_meta_get(meta, "risk_level", "low"),
            "action_profiles": _tool_meta_get(meta, "action_profiles", []),
        })
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_cached_tool_definitions(tool_registry: dict) -> List[dict]:
    """Build tool definitions with stable ordering for prompt caching."""
    signature = _tool_registry_signature(tool_registry)
    cached = _TOOL_DEFINITION_CACHE.get(signature)
    if cached is not None:
        return copy.deepcopy(cached)

    tools = []
    for tool_id, meta in sorted(tool_registry.items()):
        tools.append(tool_spec_to_openai_function({
            "tool_id": tool_id,
            "input_schema": _tool_meta_get(meta, "args_schema", _tool_meta_get(meta, "input_schema", {})),
            "description": _tool_meta_get(meta, "description", ""),
            "risk_level": _tool_meta_get(meta, "risk_level", "low"),
            "action_profiles": _tool_meta_get(meta, "action_profiles", []),
        }))
    _TOOL_DEFINITION_CACHE.clear()
    _TOOL_DEFINITION_CACHE[signature] = copy.deepcopy(tools)
    return tools


TOOL_MESSAGE_MAX_CHARS = 50_000    # Per-tool output cap fed to LLM; balances article coverage vs context pressure
ARTIFACT_ANALYSIS_MAX_CHARS = 100_000
FALLBACK_TOOL_MAX_CHARS = 2000
MAX_VALIDATION_CORRECTION_ROUNDS = 3

_PRIORITY_OUTPUT_KEYS = (
    "ok", "status", "task_id", "task", "tracking", "progress", "done",
    "report_url", "html_url", "artifact_url", "url",
    "count", "total", "success", "failed", "skipped",
    "summary", "message", "error", "reason", "title", "name", "format",
)

_BULK_TEXT_KEYS = {
    "stdout", "stderr", "log", "logs", "output", "result_output",
    "result_stdout", "result_stderr", "diff", "generated_output",
}
_LONG_CONTEXT_TEXT_KEYS = {
    "text", "content", "preview", "markdown", "document", "rendered",
}
_BULK_LIST_KEYS = {
    "rows", "items", "results", "hits", "chunks", "packets", "connections",
    "entries", "events",
}


def _compact_value_for_llm(value: Any, *, depth: int = 0, key_hint: str = "") -> Any:
    """Compact tool outputs while preserving enough evidence for follow-up."""
    key = str(key_hint or "").lower()
    if depth >= 4:
        text = str(value)
        if len(text) > 4000:
            return text[:3000] + f"\n...[truncated nested value, {len(text)} chars total]...\n" + text[-800:]
        return text
    if isinstance(value, str):
        if key in _BULK_TEXT_KEYS:
            limit = 2400
        elif key in _LONG_CONTEXT_TEXT_KEYS:
            limit = 12_000
        else:
            limit = 8000
        if len(value) > limit:
            tail = min(1000, max(0, limit // 4))
            head = max(0, limit - tail)
            return value[:head] + f"\n...[truncated {key or 'text'}, {len(value)} chars total]...\n" + (value[-tail:] if tail else "")
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        limit = 25 if key in _BULK_LIST_KEYS else (120 if depth == 0 else 50)
        compacted = [
            _compact_value_for_llm(item, depth=depth + 1, key_hint=key_hint)
            for item in value[:limit]
        ]
        if len(value) > limit:
            compacted.append({"_omitted_items": len(value) - limit})
        return compacted
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        seen: set[str] = set()
        for key in _PRIORITY_OUTPUT_KEYS:
            if key in value:
                result[key] = _compact_value_for_llm(value[key], depth=depth + 1, key_hint=key)
                seen.add(key)
        for key, val in value.items():
            if key in seen:
                continue
            result[str(key)] = _compact_value_for_llm(val, depth=depth + 1, key_hint=str(key))
        return result
    return str(value)


def _json_compact(value: Any, *, max_chars: int = TOOL_MESSAGE_MAX_CHARS) -> str:
    """JSON serialize compacted output with a valid-JSON hard cap."""
    compacted = _compact_value_for_llm(value)
    text = json.dumps(
        compacted,
        ensure_ascii=False,
        # Dict compaction deliberately inserts control fields first. Preserve
        # that order so task/status/report references survive the final hard
        # cap even when a payload also contains very large evidence fields.
        sort_keys=False,
        separators=(",", ":"),
        default=str,
    )
    if len(text) <= max_chars:
        return text

    control: dict[str, Any] = {}
    if isinstance(compacted, dict):
        for key in _PRIORITY_OUTPUT_KEYS:
            if key not in compacted:
                continue
            value = compacted[key]
            if isinstance(value, str) and len(value) > 500:
                value = value[:500] + "...[truncated]"
            candidate_control = {**control, key: value}
            candidate_envelope = {
                **candidate_control,
                "_truncated": True,
                "_original_chars": len(text),
                "_preview": "",
            }
            candidate_text = json.dumps(
                candidate_envelope,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            if len(candidate_text) <= max_chars:
                control = candidate_control
    envelope = {
        **control,
        "_truncated": True,
        "_original_chars": len(text),
        "_preview": "",
    }
    # JSON escaping can expand the preview, so find the largest prefix that
    # still keeps the entire envelope valid and within the exact character cap.
    low, high = 0, min(len(text), max(0, max_chars))
    best = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str)
    while low <= high:
        mid = (low + high) // 2
        envelope["_preview"] = text[:mid]
        candidate = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(candidate) <= max_chars:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    if len(best) <= max_chars:
        return best
    # Extremely small caller-provided caps still receive valid JSON.
    minimal = json.dumps({"_truncated": True}, separators=(",", ":"))
    return minimal if len(minimal) <= max_chars else "{}"


def _compact_tool_content(content: Any, *, max_chars: int = TOOL_MESSAGE_MAX_CHARS) -> str:
    """Compact existing tool-message content without double-encoding JSON."""
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = content
        return _json_compact(parsed, max_chars=max_chars)
    return _json_compact(content, max_chars=max_chars)


def _artifact_analysis_content(
    payload: dict[str, Any],
    *,
    max_chars: int = ARTIFACT_ANALYSIS_MAX_CHARS,
) -> str:
    """Preserve a bounded complete text artifact for one-pass analysis."""
    preview = str(payload.get("preview") or "")
    complete = bool(payload.get("content_complete", False))
    if len(preview) > max_chars:
        preview = preview[:max_chars]
        complete = False
    compacted = _compact_value_for_llm({
        key: value for key, value in payload.items() if key != "preview"
    })
    compacted["preview"] = preview
    compacted["content_complete"] = complete
    compacted["content_returned_chars"] = len(preview)
    return json.dumps(
        compacted,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


# ── Auto-Compact ────────────────────────────────────────────────────────────

DEFAULT_COMPACT_MESSAGE_TOKENS = 24_000


@dataclass
class CompactInfo:
    """Structured record of a compaction event — surfaced to metrics and LLM."""
    compacted: bool = False
    before_chars: int = 0
    after_chars: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    removed: int = 0
    saved_chars: int = 0
    tools_used: list[str] = field(default_factory=list)
    tool_stats: dict[str, dict] = field(default_factory=dict)  # {tool_name: {ok: N, failed: N}}
    key_hints: list[str] = field(default_factory=list)


def _estimate_chars(messages: List[LLMMessage]) -> int:
    """Rough character count of all messages, including tool_call JSON."""
    total = 0
    for m in messages:
        content = m.content
        if isinstance(content, list):
            total += sum(len(str(p.get("text", ""))) for p in content if isinstance(p, dict))
        else:
            total += len(str(content or ""))
        if m.tool_calls:
            total += len(json.dumps(m.tool_calls, ensure_ascii=False, default=str))
    return total


def _estimate_message_tokens(messages: List[LLMMessage]) -> int:
    """Estimate complete message cost, including roles and tool-call payloads."""
    total = 0
    for message in messages:
        total += 4  # role/framing overhead
        if isinstance(message.content, list):
            total += estimate_json_tokens(message.content)
        else:
            total += estimate_text_tokens(message.content)
        if message.tool_calls:
            total += estimate_json_tokens(message.tool_calls)
        if message.tool_call_id:
            total += estimate_text_tokens(message.tool_call_id) + 2
    return total


def _message_groups(messages: List[LLMMessage]) -> list[list[LLMMessage]]:
    """Group assistant tool calls with their result messages.

    Context compaction must never retain a tool result without the assistant
    call that created it, or retain a call while dropping its results.
    """
    groups: list[list[LLMMessage]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        group = [message]
        if message.role == "assistant" and message.tool_calls:
            call_ids = {
                str(call.get("id") or "")
                for call in message.tool_calls
                if isinstance(call, dict) and call.get("id")
            }
            cursor = index + 1
            while cursor < len(messages):
                candidate = messages[cursor]
                if candidate.role != "tool":
                    break
                if call_ids and str(candidate.tool_call_id or "") not in call_ids:
                    break
                group.append(candidate)
                cursor += 1
            index = cursor
        else:
            index += 1
        groups.append(group)
    return groups


def _priority_facts(messages: List[LLMMessage], limit: int = 12) -> list[str]:
    """Retain task/report/artifact/status/error references across compaction."""
    facts: list[str] = []
    for message in messages:
        if message.role != "tool":
            continue
        try:
            payload = json.loads(str(message.content or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        for key in _PRIORITY_OUTPUT_KEYS:
            if key not in payload or payload[key] in (None, "", [], {}):
                continue
            value = payload[key]
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
            fact = f"{key}={str(value)[:240]}"
            if fact not in facts:
                facts.append(fact)
            if len(facts) >= limit:
                return facts
    return facts


def _fit_message_to_tokens(message: LLMMessage, max_tokens: int) -> LLMMessage:
    """Shrink one oversized message without changing its protocol identity."""
    if _estimate_message_tokens([message]) <= max_tokens:
        return message
    cloned = copy.deepcopy(message)
    if cloned.tool_calls:
        call_budget = max(24, max_tokens // max(1, len(cloned.tool_calls)))
        compacted_calls = []
        for call in cloned.tool_calls:
            if not isinstance(call, dict):
                compacted_calls.append(call)
                continue
            compacted_call = copy.deepcopy(call)
            function = compacted_call.get("function")
            if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                function["arguments"], _ = truncate_text_to_tokens(
                    function["arguments"],
                    max(8, call_budget // 2),
                )
            compacted_calls.append(compacted_call)
        cloned.tool_calls = compacted_calls
    if cloned.role == "tool":
        cloned.content = _compact_tool_content(
            cloned.content,
            max_chars=max(64, int(max_tokens)),
        )
    elif isinstance(cloned.content, str):
        cloned.content, _ = truncate_text_to_tokens(cloned.content, max(32, max_tokens - 8))
    else:
        text, _ = truncate_text_to_tokens(
            json.dumps(cloned.content, ensure_ascii=False, default=str),
            max(32, max_tokens - 8),
        )
        cloned.content = text
    return cloned


def _force_messages_within_budget(
    messages: List[LLMMessage],
    token_limit: int,
) -> List[LLMMessage]:
    """Apply the final hard cap without leaving orphaned tool messages.

    Normal compaction retains rich recent context. This guard only runs when
    protocol overhead or unusually large tool-call arguments still exceed the
    provider budget after that pass.
    """
    if _estimate_message_tokens(messages) <= token_limit:
        return messages

    groups = _message_groups(messages)
    # Remove oldest non-anchor groups first. Grouping keeps assistant tool calls
    # and their role=tool results together.
    while len(groups) > 2:
        groups.pop(1)
        candidate = [message for group in groups for message in group]
        if _estimate_message_tokens(candidate) <= token_limit:
            return candidate

    # At very small budgets preserve the governing system message and the
    # newest user intent. Historical tool protocol is less important than a
    # request the model can actually answer.
    system = next((message for message in messages if message.role == "system"), None)
    latest_user = next((message for message in reversed(messages) if message.role == "user"), None)
    anchors = [message for message in (system, latest_user) if message is not None]
    if anchors:
        per_message = max(16, token_limit // len(anchors))
        fitted = [_fit_message_to_tokens(message, per_message) for message in anchors]
        if _estimate_message_tokens(fitted) <= token_limit:
            return fitted

    # The latest user request is the single most useful last resort. A plain
    # user message has no tool-call envelope, so it can always be reduced to the
    # hard limit without producing an invalid provider transcript.
    fallback = latest_user or system or messages[-1]
    if fallback.role == "tool" or fallback.tool_calls:
        fallback = LLMMessage(role="user", content="Continue the current task from the available context.")
    fitted_fallback = _fit_message_to_tokens(fallback, max(8, token_limit - 4))
    return [fitted_fallback]


def _compact_messages(
    messages: List[LLMMessage],
    *,
    max_tokens: int | None = None,
) -> tuple[List[LLMMessage], CompactInfo]:
    """Compact messages to a hard token budget while preserving tool pairs."""
    info = CompactInfo()
    token_limit = max(128, int(max_tokens or DEFAULT_COMPACT_MESSAGE_TOKENS))
    before_tokens = _estimate_message_tokens(messages)
    if before_tokens <= token_limit:
        return messages, info

    groups = _message_groups(messages)
    head_groups = groups[:2]
    remaining_groups = groups[2:]
    head = [message for group in head_groups for message in group]

    # Keep newest complete groups within roughly 70% of the message budget.
    tail_budget = max(256, int(token_limit * 0.70))
    tail_groups: list[list[LLMMessage]] = []
    used_tail_tokens = 0
    for group in reversed(remaining_groups):
        group_tokens = _estimate_message_tokens(group)
        if tail_groups and used_tail_tokens + group_tokens > tail_budget:
            break
        if not tail_groups and group_tokens > tail_budget:
            fitted: list[LLMMessage] = []
            per_message = max(128, tail_budget // max(1, len(group)))
            for message in group:
                fitted.append(_fit_message_to_tokens(message, per_message))
            group = fitted
            group_tokens = _estimate_message_tokens(group)
        tail_groups.append(group)
        used_tail_tokens += group_tokens
    tail_groups.reverse()
    middle_groups = remaining_groups[:len(remaining_groups) - len(tail_groups)]
    middle = [message for group in middle_groups for message in group]
    tail = [message for group in tail_groups for message in group]

    # If there is no removable middle, hard-fit oversized retained messages.
    if not middle:
        per_message = max(96, token_limit // max(1, len(messages)))
        compacted = [_fit_message_to_tokens(message, per_message) for message in messages]
        compacted = _force_messages_within_budget(compacted, token_limit)
        info.compacted = True
        info.before_chars = _estimate_chars(messages)
        info.after_chars = _estimate_chars(compacted)
        info.before_tokens = before_tokens
        info.after_tokens = _estimate_message_tokens(compacted)
        info.removed = 0
        info.saved_chars = info.before_chars - info.after_chars
        return compacted, info

    middle_count = len(middle)

    # ── Collect tool calls and their actual role=tool results ──
    tool_names: list[str] = []
    tool_stats: dict[str, dict] = {}
    call_names: dict[str, str] = {}
    for m in middle:
        if not m.tool_calls:
            continue
        for tc in m.tool_calls:
            name = tc.get("name", tc.get("function", {}).get("name", ""))
            if not name:
                continue
            if name not in tool_names:
                tool_names.append(name)
            if name not in tool_stats:
                tool_stats[name] = {"ok": 0, "failed": 0, "total": 0}
            tool_stats[name]["total"] += 1
            call_id = str(tc.get("id") or "")
            if call_id:
                call_names[call_id] = name

    key_hints: list[str] = []
    tool_summaries: list[str] = []
    for m in middle:
        if m.role != "tool" or not m.tool_call_id:
            continue
        try:
            result = json.loads(str(m.content or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            result = {}
        if not isinstance(result, dict):
            continue
        name = call_names.get(str(m.tool_call_id), "")
        if name in tool_stats:
            if result.get("ok", True):
                tool_stats[name]["ok"] += 1
            else:
                tool_stats[name]["failed"] += 1
        summary = result.get("summary")
        if isinstance(summary, str):
            snippet = summary[:200].replace("\n", " ").strip()
            if snippet and snippet not in tool_summaries and len(tool_summaries) < 3:
                tool_summaries.append(snippet)
        hint = result.get("_hint")
        if isinstance(hint, str) and hint.strip() and len(key_hints) < 3:
            key_hints.append(hint[:200].replace("\n", " ").strip())

    # Merge tool summaries and key_hints so they appear in compact summary
    priority_facts = _priority_facts(middle)
    combined_hints = (["; ".join(priority_facts)] if priority_facts else []) + tool_summaries + key_hints

    # ── Build compact summary ──
    before = _estimate_chars(messages)
    summary = _build_compact_summary(middle_count, tool_names, tool_stats, combined_hints)
    summary_message = LLMMessage(role="system", content=summary)
    compacted = head + [summary_message] + tail

    # Enforce a real post-compaction hard cap. First shrink the generated
    # summary, then retained non-system messages, while keeping tool groups.
    if _estimate_message_tokens(compacted) > token_limit:
        summary_budget = max(128, token_limit // 8)
        summary_message = _fit_message_to_tokens(summary_message, summary_budget)
        compacted = head + [summary_message] + tail
    if _estimate_message_tokens(compacted) > token_limit:
        fixed_head = [_fit_message_to_tokens(m, max(32, token_limit // 6)) for m in head]
        remaining = max(256, token_limit - _estimate_message_tokens(fixed_head + [summary_message]))
        fitted_tail: list[LLMMessage] = []
        per_tail = max(32, remaining // max(1, len(tail)))
        for message in tail:
            fitted_tail.append(_fit_message_to_tokens(message, per_tail))
        compacted = fixed_head + [summary_message] + fitted_tail
    compacted = _force_messages_within_budget(compacted, token_limit)
    info.compacted = True
    info.before_chars = before
    info.after_chars = _estimate_chars(compacted)
    info.before_tokens = before_tokens
    info.after_tokens = _estimate_message_tokens(compacted)
    info.removed = middle_count
    info.saved_chars = before - info.after_chars
    info.tools_used = tool_names
    info.tool_stats = tool_stats
    info.key_hints = combined_hints
    return compacted, info


def _build_compact_summary(
    turns: int, tools: list[str], tool_stats: dict, hints: list[str],
) -> str:
    """Build an LLM-readable compact summary with tool stats and key findings."""
    parts = [f"[{turns} earlier turns compacted."]

    # Tool usage summary
    if tool_stats:
        tool_parts = []
        for name in tools[:6]:
            stats = tool_stats.get(name, {})
            ok = stats.get("ok", 0)
            failed = stats.get("failed", 0)
            if failed:
                tool_parts.append(f"{name}: {ok}✓ {failed}✗")
            else:
                tool_parts.append(f"{name}: {ok} calls")
        if tool_parts:
            parts.append("Tools: " + ", ".join(tool_parts) + ".")

    # Key findings
    if hints:
        parts.append("Key findings: " + "; ".join(hints[:3]) + ".")

    parts.append("Full context in latest messages below.]")
    return " ".join(parts)


# ── Streaming Tool Executor ─────────────────────────────────────────────────

@dataclass
class StreamingToolResult:
    tool_name: str
    call_id: str
    output: dict
    ok: bool
    error: Optional[str] = None
    latency_ms: float = 0.0


class StreamingToolExecutor:
    """Execute tools as they arrive from the LLM stream.

    Read-only tools run in parallel; write tools serialised.
    """

    def __init__(self, tool_runtime, config: SSOTRuntimeConfig | None = None, emitter=None):
        self._runtime = tool_runtime
        self._config = config or SSOTRuntimeConfig()
        self._emitter = emitter
        self.max_parallel_width = 0

    def _is_read_only_call(self, tool_call: LLMToolCall) -> bool:
        """Classify concurrency from the canonical tool action.

        Merged tools contain both read and write actions, so tool-id-only
        classification is unsafe. Unknown or missing actions are serialized.
        """
        from .contracts import is_read_only_call

        return is_read_only_call(tool_call.name, tool_call.arguments)

    async def execute(
        self,
        tool_calls: List[LLMToolCall],
        *,
        ctx: StatelessContext | None = None,
        budget=None,
    ) -> List[StreamingToolResult]:
        """Execute tool calls. Read-only parallel, writes serialised.

        Returns results in the ORIGINAL tool_calls order so callers can
        safely zip(results, tool_calls) for idempotent-key tracking.
        """
        # Build result map keyed by call_id so we can return in original order.
        # Consecutive reads may run together, but every write is an ordering
        # barrier. Executing all reads before all writes changes semantics for
        # batches such as [read, write, read].
        result_by_id: dict[str, StreamingToolResult] = {}

        async def execute_read_group(group: list[LLMToolCall]) -> None:
            if not group:
                return
            self.max_parallel_width = max(self.max_parallel_width, len(group))
            tasks = [self._execute_one(tc, ctx=ctx, budget=budget) for tc in group]
            # return_exceptions=True: collect every result, even if some fail
            ro_results = await asyncio.gather(*tasks, return_exceptions=True)
            for tc, r in zip(group, ro_results):
                if isinstance(r, Exception):
                    result_by_id[tc.id] = StreamingToolResult(
                        tool_name=tc.name,
                        call_id=tc.id,
                        output={},
                        ok=False,
                        error=str(r),
                    )
                else:
                    result_by_id[tc.id] = r

        read_group: list[LLMToolCall] = []
        for tc in tool_calls:
            if self._is_read_only_call(tc):
                read_group.append(tc)
                continue
            await execute_read_group(read_group)
            read_group = []
            self.max_parallel_width = max(self.max_parallel_width, 1)
            result_by_id[tc.id] = await self._execute_one(tc, ctx=ctx, budget=budget)
        await execute_read_group(read_group)

        # Return in original order
        return [result_by_id[tc.id] for tc in tool_calls]

    async def _execute_one(
        self,
        tc: LLMToolCall,
        *,
        ctx: StatelessContext | None = None,
        budget=None,
    ) -> StreamingToolResult:
        """Execute a single tool call via the tool runtime client."""
        tool_id = tc.name.replace("__", ".")
        if ctx is not None and hasattr(self._runtime, "execute_node"):
            node = ExecutionNode(
                id=tc.id,
                tool=tool_id,
                args=dict(tc.arguments or {}),
            )
            result = await self._runtime.execute_node(node, ctx, {})
            if not result.success:
                result = await self._maybe_retry_node(node, ctx, result, budget)
            return self._from_tool_result(result, fallback_call_id=tc.id)

        try:
            # Map LLM name (dots → underscores) back to canonical tool_id
            _t0 = time.monotonic()
            result = await asyncio.to_thread(
                self._runtime.invoke_raw, tool_id, tc.arguments
            )
            _latency = (time.monotonic() - _t0) * 1000
            return StreamingToolResult(
                tool_name=tool_id,
                call_id=tc.id,
                output=result,
                ok=result.get("ok", False),
                error=result.get("error"),
                latency_ms=float(_latency),
            )
        except Exception as e:
            return StreamingToolResult(
                tool_name=tc.name,
                call_id=tc.id,
                output={},
                ok=False,
                error=str(e),
            )

    async def _maybe_retry_node(
        self,
        node: ExecutionNode,
        ctx: StatelessContext,
        original_result: ToolResult,
        budget,
    ) -> ToolResult:
        from .contracts import get_retry_contract
        from .tool_retry_policy import should_retry_tool_failure

        contract = get_retry_contract(node.tool, node.args)
        current_result = original_result
        total_latency_ms = float(original_result.latency_ms or 0.0)

        while not current_result.success:
            error_code = self._retry_error_code(current_result)
            budget_ok = bool(budget.check_execution().ok) if budget is not None else True
            decision = should_retry_tool_failure(
                node=node,
                tool_contract=contract,
                error_code=error_code,
                error_message=current_result.error or "",
                config_max_retries=(
                    int(getattr(contract, "max_retries", 0) or 0)
                    if contract is not None else 0
                ),
                global_max_retries_per_node=self._config.max_retries_per_node,
                budget_ok=budget_ok,
            )
            event_index = self._record_retry_decision(ctx, node, decision)
            if not decision.retry_allowed:
                return current_result

            await asyncio.sleep(decision.backoff_ms / 1000.0)
            # A retry that was legal before backoff may no longer fit in the
            # request budget afterwards. Never start it once the deadline has
            # elapsed.
            if budget is not None and not budget.check_execution().ok:
                self._record_retry_aborted(ctx, event_index, "budget_exceeded_after_backoff")
                return current_result

            node.retry_count += 1
            retry_started = time.monotonic()
            current_result = await self._runtime.execute_node(node, ctx, {})
            retry_duration_ms = (time.monotonic() - retry_started) * 1000
            total_latency_ms += retry_duration_ms + float(decision.backoff_ms)
            current_result.retry_count = node.retry_count
            current_result.metadata = dict(current_result.metadata or {})
            current_result.metadata.update({
                "retried": True,
                "retry_count": node.retry_count,
                "retry_reason": decision.reason,
                "retry_backoff_ms": decision.backoff_ms,
                "retry_error_code": decision.error_code,
                "retry_original_error": decision.notes.get("original_error", ""),
                "retry_total_latency_ms": total_latency_ms,
            })
            self._record_retry_result(
                ctx, node, current_result,
                event_index=event_index,
                duration_ms=retry_duration_ms,
            )

        return current_result

    @staticmethod
    def _retry_error_code(result: ToolResult) -> str:
        error_code = (result.error_code or "").strip().upper()
        # Generic handler failure carries no retry semantics. Infer only a
        # narrow set of transient classes from the normalized error text.
        if error_code and error_code != "TOOL_RETURNED_NOT_OK":
            return error_code
        err = (result.error or "").lower()
        if any(token in err for token in ("authentication", "permission denied", "password", "credential")):
            return "CREDENTIAL_ACCESS"
        if any(token in err for token in (
            "security check failed", "forbidden", "policy blocked",
            "not allowed", "blocked:", "workspace_mismatch",
        )):
            return "POLICY_BLOCKED"
        if "timeout" in err or "timed out" in err:
            return "TOOL_TIMEOUT"
        if "rate" in err and "limit" in err:
            return "RATE_LIMITED"
        if "connection" in err and "reset" in err:
            return "CONNECTION_RESET"
        for status in (429, 500, 502, 503, 504):
            if f"http {status}" in err or f"status {status}" in err:
                return f"HTTP_{status}"
        if any(token in err for token in (
            " is required", "invalid ", "unknown action", "unsupported ",
            "not found", "no such ", "does not exist", "_not_found",
            "not_found", "_required", "unsupported_", "unknown_",
            "artifact_empty", "empty_document",
        )):
            return "ARGS_INVALID"
        return "TOOL_EXCEPTION"

    @staticmethod
    def _record_retry_decision(ctx: StatelessContext, node: ExecutionNode, decision) -> int:
        events = list(ctx.extras.get("retry_events") or [])
        events.append({
            **decision.to_dict(),
            "node_id": node.id,
            "tool_id": node.tool,
        })
        ctx.extras["retry_events"] = events
        summary = dict(ctx.extras.get("retry_summary") or {
            "retry_attempts": 0,
            "retried_nodes": [],
            "retry_succeeded": 0,
            "retry_failed": 0,
            "retry_blocked": 0,
        })
        if not decision.retry_allowed:
            summary["retry_blocked"] = int(summary.get("retry_blocked", 0) or 0) + 1
        ctx.extras["retry_summary"] = summary
        return len(events) - 1

    @staticmethod
    def _record_retry_result(
        ctx: StatelessContext,
        node: ExecutionNode,
        result: ToolResult,
        *,
        event_index: int,
        duration_ms: float,
    ) -> None:
        summary = dict(ctx.extras.get("retry_summary") or {
            "retry_attempts": 0,
            "retried_nodes": [],
            "retry_succeeded": 0,
            "retry_failed": 0,
            "retry_blocked": 0,
        })
        summary["retry_attempts"] = int(summary.get("retry_attempts", 0) or 0) + 1
        nodes = list(summary.get("retried_nodes") or [])
        if node.id not in nodes:
            nodes.append(node.id)
        summary["retried_nodes"] = nodes
        if result.success:
            summary["retry_succeeded"] = int(summary.get("retry_succeeded", 0) or 0) + 1
        else:
            summary["retry_failed"] = int(summary.get("retry_failed", 0) or 0) + 1
        ctx.extras["retry_summary"] = summary
        events = list(ctx.extras.get("retry_events") or [])
        if 0 <= event_index < len(events):
            events[event_index] = {
                **events[event_index],
                "attempt": node.retry_count,
                "final_status": "succeeded" if result.success else "failed",
                "duration_ms": round(float(duration_ms or 0.0), 3),
                "result_error_code": result.error_code or "",
            }
            ctx.extras["retry_events"] = events

    @staticmethod
    def _record_retry_aborted(ctx: StatelessContext, event_index: int, reason: str) -> None:
        events = list(ctx.extras.get("retry_events") or [])
        if 0 <= event_index < len(events):
            events[event_index] = {
                **events[event_index],
                "retry_allowed": False,
                "blocked_by_policy": False,
                "final_status": "aborted",
                "reason": reason,
            }
            ctx.extras["retry_events"] = events
        summary = dict(ctx.extras.get("retry_summary") or {})
        summary["retry_blocked"] = int(summary.get("retry_blocked", 0) or 0) + 1
        ctx.extras["retry_summary"] = summary

    @staticmethod
    def _from_tool_result(result: ToolResult, *, fallback_call_id: str) -> StreamingToolResult:
        output = result.data if isinstance(result.data, dict) else {"data": result.data}
        if not result.success and result.error:
            output = {**(output or {}), "error": result.error}
        metadata = dict(result.metadata or {})
        if result.retry_count:
            metadata["retry_count"] = result.retry_count
        if metadata:
            output = {**(output or {}), "metadata": metadata}
        return StreamingToolResult(
            tool_name=result.tool,
            call_id=result.node_id or fallback_call_id,
            output=output or {},
            ok=bool(result.success),
            error=result.error,
            latency_ms=float(result.latency_ms or 0.0),
        )


# ── QueryLoop ────────────────────────────────────────────────────────────────

@dataclass
class QueryLoopResult:
    final_response: str
    tool_results: List[StreamingToolResult] = field(default_factory=list)
    iterations: int = 0
    total_tool_calls: int = 0
    llm_calls: int = 0
    error: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    risk_level: str = "low"
    approval_required: bool = False
    approval_nodes: list[str] = field(default_factory=list)
    approval_details: list[dict[str, Any]] = field(default_factory=list)
    hard_block: bool = False
    metrics: Dict[str, Any] = field(default_factory=dict)


class QueryLoop:
    """Iterative LLM + tool execution loop.

    Usage:
        loop = QueryLoop(config, tool_registry, tool_runtime, llm_invoke, emitter)
        result = await loop.run(ctx, budget, metrics)
    """

    def __init__(
        self,
        config: SSOTRuntimeConfig,
        tool_registry: dict[str, dict[str, Any]],
        tool_runtime,
        llm_invoke: Callable[..., Any] | None = None,
        emitter=None,
        approval_handler: Callable[[StatelessContext, dict[str, Any]], Any] | None = None,
    ):
        self._config = config
        self._tool_registry = tool_registry
        self._tool_runtime = tool_runtime
        self._llm_invoke = llm_invoke
        self._emitter = emitter
        self._approval_handler = approval_handler
        self._executor = StreamingToolExecutor(tool_runtime, config, emitter)
        self._cached_tools = _build_cached_tool_definitions(tool_registry)
        self._context_budget = RuntimeContextBudget.build(
            tools=self._cached_tools,
            context_window_tokens=config.context_window_tokens,
            max_input_tokens=config.max_input_tokens,
            reserved_output_tokens=config.max_output_tokens,
            safety_tokens=config.context_safety_tokens,
        )
        self._llm_call_count = 0

    async def run(
        self,
        ctx: StatelessContext,
        budget,
        metrics,
    ) -> QueryLoopResult:
        """Run the full query loop."""
        t_start = time.monotonic()
        all_results: List[StreamingToolResult] = []
        iterations = 0
        llm_calls = 0
        # Doom-loop detection: key=(tool, args_hash) → consecutive_failures
        failure_counts: Dict[str, int] = {}
        validation_correction_attempts = 0
        completed_call_keys: set[str] = set()
        used_call_ids: set[str] = set()
        execution_duration_ms = 0.0
        output_truncated = False
        output_truncation_reason = ""

        # Build initial messages (cacheable prefix)
        messages = self._build_initial(ctx)

        max_iterations = getattr(self._config, "max_query_loop_iterations", 20)

        def finish(**values) -> QueryLoopResult:
            """Build every exit projection with the same runtime metrics."""
            projected_metrics = {
                "elapsed_ms": (time.monotonic() - t_start) * 1000,
                "iterations": iterations,
                "tool_calls": len(all_results),
                "llm_calls": values.get("llm_calls", llm_calls),
                "context_estimated_chars": _estimate_chars(messages),
                "context_estimated_tokens": _estimate_message_tokens(messages),
                "context_compacted": (
                    metrics.snapshot().context_compacted if metrics else False
                ),
                "context_budget": self._context_budget.as_dict(),
                "execution_duration_ms": execution_duration_ms,
                "max_parallel_width": self._executor.max_parallel_width,
                "validation_corrections": validation_correction_attempts,
                "output_truncated": output_truncated,
                "output_truncation_reason": output_truncation_reason,
            }
            projected_metrics.update(dict(values.pop("metrics", {}) or {}))
            values.setdefault("tool_results", all_results)
            values.setdefault("iterations", iterations)
            values.setdefault("total_tool_calls", len(all_results))
            values.setdefault("llm_calls", llm_calls)
            return QueryLoopResult(metrics=projected_metrics, **values)

        # Trusted UI workflows may hand off explicit artifact ids after a
        # background task completes. Read those workspace-scoped artifacts
        # through the canonical runtime before planning, then use one
        # final-response-only LLM call when the content is complete.
        if self._is_cancelled(ctx):
            return finish(final_response="任务已取消。", error="cancelled_by_user")
        prefetch_ids = list(dict.fromkeys(
            str(value).strip()
            for value in (ctx.extras.get("prefetch_artifact_ids") or [])
            if str(value).strip()
        ))[:8]
        if prefetch_ids and self._tool_runtime.has_tool("workspace.artifact"):
            prefetch_calls = [
                LLMToolCall(
                    id=f"prefetch_artifact_{index}",
                    name="workspace.artifact",
                    arguments={"action": "read", "artifact_id": artifact_id},
                )
                for index, artifact_id in enumerate(prefetch_ids)
            ]
            used_call_ids.update(call.id for call in prefetch_calls)
            execution_started = time.monotonic()
            budget.begin_execution()
            try:
                prefetch_results = await self._executor.execute(
                    prefetch_calls,
                    ctx=ctx,
                    budget=budget,
                )
            finally:
                budget.end_execution()
                execution_duration_ms += (time.monotonic() - execution_started) * 1000
            all_results.extend(prefetch_results)
            messages = self._append_tool_round(
                messages,
                prefetch_calls,
                prefetch_results,
            )
            if self._has_complete_analysis_artifact(prefetch_results):
                messages = self._append_turn_nudge(
                    messages,
                    RESPONSE_ONLY_MARKER
                    + " Complete artifacts were prefetched above. Analyze them and "
                    "answer the original request now; do not call tools.",
                )

        if ctx.extras.get("response_only") and not self._is_response_only(messages):
            reason = str(ctx.extras.get("response_only_reason") or "caller_requested")
            messages = self._append_turn_nudge(
                messages,
                RESPONSE_ONLY_MARKER
                + f" Response-only mode ({reason}). Use the facts already supplied, "
                "do not call tools, and do not invent missing evidence.",
            )

        while iterations < max_iterations:
            if self._is_cancelled(ctx):
                # If tools already produced results, surface them as a
                # fallback instead of discarding everything.  This avoids
                # losing completed work when the WebSocket closes before
                # the final LLM summarisation call finishes.
                if all_results:
                    return finish(
                        final_response=self._build_tool_result_fallback(ctx, all_results),
                        tool_results=all_results,
                        iterations=iterations,
                        total_tool_calls=len(all_results),
                        llm_calls=budget.llm_calls,
                        error="cancelled_by_user",
                    )
                return finish(
                    final_response="任务已取消。",
                    error="cancelled_by_user",
                )
            iterations += 1

            # Budget check. BudgetController is the SSOT for LLM call count;
            # local llm_calls mirrors it for QueryLoopResult only.
            budget_status = budget.check_llm_call()
            if not budget_status.ok:
                return finish(
                    final_response=(
                        "已达到 LLM 调用上限，请简化请求。"
                        if not all_results
                        else self._build_tool_result_fallback(ctx, all_results)
                    ),
                    tool_results=all_results,
                    iterations=iterations,
                    total_tool_calls=len(all_results),
                    llm_calls=budget.llm_calls,
                    error=budget_status.exceeded or "budget_exceeded",
                )

            # Auto-compact with context tracking
            _before_tokens = _estimate_message_tokens(messages)
            if _before_tokens > self._context_budget.message_tokens:
                messages, _compact_info = _compact_messages(
                    messages,
                    max_tokens=self._context_budget.message_tokens,
                )
                if _compact_info.compacted and metrics is not None:
                    metrics.mark_compacted(_compact_info)
            if metrics is not None:
                metrics.capture_context_usage(
                    _estimate_chars(messages),
                    estimated_tokens=_estimate_message_tokens(messages),
                    budget_tokens=self._context_budget.message_tokens,
                )

            # Call LLM (with streaming for tool exec)
            response = await self._call_llm(messages, ctx)
            if response is not None and (response.metadata or {}).get("output_truncated"):
                output_truncated = True
                output_truncation_reason = str(
                    (response.metadata or {}).get("truncation_reason") or response.finish_reason or "unknown"
                )

            if response is None or response.error:
                final_resp: str
                if all_results:
                    final_resp = self._build_tool_result_fallback(ctx, all_results)
                elif response is not None and response.content and response.content.strip():
                    final_resp = response.content.strip()
                elif response is not None:
                    final_resp = "LLM 调用失败"
                else:
                    final_resp = "LLM 调用失败"
                return finish(
                    final_response=final_resp,
                    tool_results=all_results,
                    iterations=iterations,
                    total_tool_calls=len(all_results),
                    llm_calls=budget.llm_calls,
                    error=response.error if response else "no_response",
                )

            llm_calls = budget.llm_calls

            # Check for tool calls
            if response.tool_calls:
                # Convert to LLMToolCall objects
                tool_calls = self._parse_tool_calls(response.tool_calls)
                tool_calls = self._unique_call_ids(tool_calls, iterations, used_call_ids)

                gate = self._prepare_tool_calls(ctx, tool_calls)
                if (
                    not gate["ok"]
                    and gate.get("approval_required")
                    and not gate.get("hard_block")
                    and self._approval_handler is not None
                ):
                    if self._emitter:
                        self._emitter.emit("approval_required", {
                            "risk_level": gate.get("risk_level", "high"),
                            "approval_nodes": list(gate.get("approval_nodes") or []),
                        })
                    decision = self._approval_handler(ctx, gate)
                    if inspect.isawaitable(decision):
                        decision = await decision
                    if not bool(decision):
                        ctx.extras["approval_resolved"] = True
                        ctx.extras["approval_allowed"] = False
                        return finish(
                            final_response="操作已取消（审批未通过）。",
                            tool_results=all_results,
                            iterations=iterations,
                            total_tool_calls=len(all_results),
                            llm_calls=llm_calls,
                            risk_level=gate.get("risk_level", "high"),
                            approval_required=False,
                            metrics={"approval_denied": True},
                        )
                    approved_node_ids = set(gate.get("approval_nodes") or [])
                    approved_keys = set(ctx.extras.get("approved_tool_call_keys") or [])
                    approved_keys.update(
                        self._tool_call_key(call)
                        for call in tool_calls
                        if not approved_node_ids or call.id in approved_node_ids
                    )
                    ctx.extras["approved_tool_call_keys"] = sorted(approved_keys)
                    ctx.extras["approval_resolved"] = True
                    ctx.extras["approval_allowed"] = True
                    gate = self._prepare_tool_calls(ctx, tool_calls)
                if not gate["ok"]:
                    if gate.get("hard_block") or gate.get("approval_nodes") or gate.get("approval_required"):
                        return finish(
                            final_response=gate["message"],
                            tool_results=all_results,
                            iterations=iterations,
                            total_tool_calls=len(all_results),
                            llm_calls=llm_calls,
                            error=gate["error"],
                            errors=list(gate.get("errors") or []),
                            risk_level=gate.get("risk_level", "high"),
                            approval_required=bool(gate.get("approval_required", False)),
                            approval_nodes=list(gate.get("approval_nodes") or []),
                            approval_details=list(gate.get("approval_details") or []),
                            hard_block=bool(gate.get("hard_block", False)),
                        )
                    # Soft validation errors (e.g. missing_required_arg) —
                    # feed back to LLM as tool results so it can correct itself.
                    if validation_correction_attempts >= MAX_VALIDATION_CORRECTION_ROUNDS:
                        ctx.extras["validation_correction_exhausted"] = True
                        return finish(
                            final_response=(
                                "工具参数连续校验失败，已停止自动修正。\n"
                                + gate["message"]
                            ),
                            tool_results=all_results,
                            iterations=iterations,
                            total_tool_calls=len(all_results),
                            llm_calls=llm_calls,
                            error="validation_correction_exhausted",
                            errors=list(gate.get("errors") or []),
                            risk_level="low",
                        )
                    validation_correction_attempts += 1
                    if self._emitter:
                        self._emitter.emit("tool_validation_failed", {
                            "errors": gate.get("errors", []),
                            "message": gate["message"],
                            "attempt": validation_correction_attempts,
                            "max_attempts": MAX_VALIDATION_CORRECTION_ROUNDS,
                        })
                    structured_errors = list(gate.get("validation_errors") or [])
                    ctx.extras.setdefault("validation_correction_events", []).append({
                        "attempt": validation_correction_attempts,
                        "max_attempts": MAX_VALIDATION_CORRECTION_ROUNDS,
                        "errors": structured_errors,
                    })
                    fake_results = [
                        StreamingToolResult(
                            tool_name=tc.name,
                            call_id=tc.id,
                            output={
                                "ok": False,
                                "executed": False,
                                "retryable": True,
                                "error_code": "TOOL_ARGUMENT_VALIDATION_FAILED",
                                "error": gate["message"],
                                "validation_errors": structured_errors,
                                "correction_attempt": validation_correction_attempts,
                                "max_correction_attempts": MAX_VALIDATION_CORRECTION_ROUNDS,
                                "instruction": (
                                    "Correct the reported tool arguments and issue a new call. "
                                    "Do not repeat unchanged invalid arguments."
                                ),
                            },
                            ok=False,
                            error=gate["message"],
                        )
                        for tc in tool_calls
                    ]
                    all_results.extend(fake_results)
                    messages = self._append_tool_round(messages, tool_calls, fake_results)
                    # Don't count these as successful tool calls
                    continue
                tool_calls = gate["tool_calls"]

                # Deduplicate only after deterministic alias/argument repair.
                # This lets the model recover with changed arguments while
                # preventing an identical successful or failed operation from
                # running forever. The old pre-gate comparison missed aliases
                # such as file_read -> read because their raw keys differed.
                repeated_calls = [
                    tc for tc in tool_calls
                    if self._tool_call_key(tc) in completed_call_keys
                ]
                if repeated_calls and len(repeated_calls) == len(tool_calls):
                    return finish(
                        final_response=self._build_tool_result_fallback(ctx, all_results),
                        error="duplicate_tool_call",
                    )
                tool_calls = [
                    tc for tc in tool_calls
                    if self._tool_call_key(tc) not in completed_call_keys
                ]
                if not tool_calls:
                    return finish(
                        final_response=self._build_tool_result_fallback(ctx, all_results),
                        error="duplicate_tool_call",
                    )

                # Execute tools (parallel read-only, serial writes)
                execution_started = time.monotonic()
                budget.begin_execution()
                try:
                    results = await self._executor.execute(tool_calls, ctx=ctx, budget=budget)
                    all_results.extend(results)
                    for tc in tool_calls:
                        completed_call_keys.add(self._tool_call_key(tc))

                    # ── Tracking: auto-poll producer-declared long tasks ──
                    polled_results = await self._settle_tracking(ctx, results, budget=budget)
                finally:
                    budget.end_execution()
                    execution_duration_ms += (time.monotonic() - execution_started) * 1000
                if polled_results:
                    all_results.extend(polled_results)
                    results = results + polled_results

                # Append assistant message (with tool_calls) + tool results
                messages = self._append_tool_round(messages, tool_calls, results)
                failed_results = [result for result in results if not result.ok]
                if failed_results:
                    recovery_nudge = self._build_tool_failure_recovery_nudge(failed_results)
                    messages = self._append_turn_nudge(messages, recovery_nudge)
                    ctx.extras.setdefault("tool_recovery_events", []).append({
                        "iteration": iterations,
                        "failed_tools": [result.tool_name for result in failed_results],
                        "errors": [str(result.error or "")[:240] for result in failed_results],
                    })
                if self._has_complete_analysis_artifact(results):
                    messages = self._append_turn_nudge(
                        messages,
                    RESPONSE_ONLY_MARKER
                        + " The complete artifact content is included above. "
                        "Analyze it and answer the original request now; do not read files or call tools.",
                    )

                # ── Doom-loop detection ──
                for r in results:
                    if not r.ok and r.error:
                        err_lower = str(r.error).lower()
                        # Tool not found (wrong name)
                        if "not found" in err_lower:
                            key = f"not_found:{r.tool_name}"
                            failure_counts[key] = failure_counts.get(key, 0) + 1
                            if failure_counts[key] >= 3:
                                return finish(
                                    final_response=f"工具 {r.tool_name} 不存在，已尝试 {failure_counts[key]} 次。请检查工具名称是否正确。",
                                    tool_results=all_results,
                                    iterations=iterations,
                                    total_tool_calls=len(all_results),
                                    llm_calls=llm_calls,
                                    error="doom_loop",
                                )
                        # Authentication failure — do NOT retry unchanged credentials.
                        if "authentication" in err_lower or "password" in err_lower or "permission denied" in err_lower or "auth" in err_lower:
                            key = f"auth:{r.tool_name}"
                            failure_counts[key] = failure_counts.get(key, 0) + 1
                            if failure_counts[key] >= 2:
                                return finish(
                                    final_response=(
                                        f"认证或权限错误已连续失败 {failure_counts[key]} 次。"
                                        "请检查凭据、权限或访问目标后再重试。"
                                    ),
                                    tool_results=all_results,
                                    iterations=iterations,
                                    total_tool_calls=len(all_results),
                                    llm_calls=llm_calls,
                                    error="doom_loop_auth",
                                )
                        # Budget exhaustion — stop immediately
                        if "budget" in err_lower or "exceeded" in err_lower:
                            return finish(
                                final_response=(
                                    self._build_tool_result_fallback(ctx, all_results)
                                    if all_results
                                    else "已达到 LLM 调用或工具执行预算上限。请简化请求或稍后再试。"
                                ),
                                tool_results=all_results,
                                iterations=iterations,
                                total_tool_calls=len(all_results),
                                llm_calls=llm_calls,
                                error="doom_loop_budget",
                            )
                        # Missing/invalid arguments are deterministic. Different
                        # call ids must not allow the same schema error to consume
                        # the entire execution budget.
                        if "required" in err_lower or "invalid argument" in err_lower:
                            normalized_error = " ".join(err_lower.split())[:160]
                            key = f"args:{r.tool_name}:{normalized_error}"
                            failure_counts[key] = failure_counts.get(key, 0) + 1
                            if failure_counts[key] >= 2:
                                return finish(
                                    final_response=self._build_tool_result_fallback(ctx, all_results),
                                    tool_results=all_results,
                                    iterations=iterations,
                                    total_tool_calls=len(all_results),
                                    llm_calls=llm_calls,
                                    error="doom_loop_args",
                                )
                        # Timeout / connection — generic doom-loop detection
                        if "timeout" in err_lower or "timed out" in err_lower or "connection" in err_lower or "network" in err_lower:
                            key = f"timeout:{r.tool_name}:{_json_compact(r.output, max_chars=600)}"
                            failure_counts[key] = failure_counts.get(key, 0) + 1
                            if failure_counts[key] >= 3:
                                return finish(
                                    final_response=f"工具 {r.tool_name} 连续超时 {failure_counts[key]} 次。请检查网络连接或设备可达性。",
                                    tool_results=all_results,
                                    iterations=iterations,
                                    total_tool_calls=len(all_results),
                                    llm_calls=llm_calls,
                                    error="doom_loop_timeout",
                                )

                continue

            # No tool calls → final response
            final_text = response.content or ""
            if not final_text.strip():
                if all_results and iterations < max_iterations:
                    reminder = (
                        RESPONSE_ONLY_MARKER
                        + " You just received tool results. "
                        "Now answer the user's original question in natural language. "
                        "Do NOT call any more tools — produce the final response directly."
                    )
                    messages.append(LLMMessage(role="user", content=reminder))
                    continue
                elif all_results:
                    # iterations >= max_iterations or nudge already tried enough
                    final_text = self._build_tool_result_fallback(ctx, all_results)
                else:
                    final_text = "抱歉，我无法生成回复。请重新描述您的问题后再试。"
            else:
                final_text = final_text.strip()
            elapsed = (time.monotonic() - t_start) * 1000

            return finish(
                final_response=final_text,
                tool_results=all_results,
                iterations=iterations,
                total_tool_calls=len(all_results),
                llm_calls=llm_calls,
                metrics={
                    "elapsed_ms": elapsed,
                    "iterations": iterations,
                    "tool_calls": len(all_results),
                    "llm_calls": llm_calls,
                    "context_estimated_chars": _estimate_chars(messages),
                    "context_estimated_tokens": _estimate_message_tokens(messages),
                    "context_compacted": metrics.snapshot().context_compacted if metrics else False,
                    "context_budget": self._context_budget.as_dict(),
                    "execution_duration_ms": execution_duration_ms,
                    "max_parallel_width": self._executor.max_parallel_width,
                    "output_truncated": output_truncated,
                    "output_truncation_reason": output_truncation_reason,
                },
            )

        # Max iterations exhausted
        return finish(
            final_response=(
                self._build_tool_result_fallback(ctx, all_results)
                if all_results else "已达到最大迭代次数，请缩小任务范围后重试。"
            ),
            tool_results=all_results,
            iterations=iterations,
            total_tool_calls=len(all_results),
            llm_calls=llm_calls,
            error="max_iterations",
        )

    # ── Private helpers ──────────────────────────────────────────────────

    @staticmethod
    def _should_poll_tracking(user_input: str, tracking: dict) -> bool:
        """Track every producer-declared long task within runtime budgets."""
        if tracking.get("done"):
            return False
        action = str(tracking.get("suggested_next_action") or "").lower()
        if action and action != "poll_get":
            return False
        return str(tracking.get("kind") or "") == "long_task"

    def _build_initial(self, ctx: StatelessContext) -> List[LLMMessage]:
        """Build initial messages with cacheable prefix."""
        conversation_block = ctx.extras.get("conversation_history_block") or ""
        retrieved_block = ctx.extras.get("retrieved_context_block") or ""
        operational_hint = ctx.extras.get("operational_clarification") or {}
        runtime_guidance = ""
        if isinstance(operational_hint, dict):
            runtime_guidance = str(operational_hint.get("guidance") or "")

        return [
            LLMMessage(
                role="system",
                content=build_runtime_system_prompt(ctx.extras),
            ),
            LLMMessage(role="user", content=build_turn_message(
                workspace_id=ctx.workspace_id,
                session_id=ctx.session_id,
                user_input=ctx.user_input,
                conversation_history=str(conversation_block),
                governed_context=str(retrieved_block),
                runtime_guidance=runtime_guidance,
            )),
        ]

    @staticmethod
    def _unique_call_ids(
        tool_calls: List[LLMToolCall],
        iteration: int,
        used: set[str],
    ) -> List[LLMToolCall]:
        """Keep provider call ids unique across iterative LLM rounds."""
        result: list[LLMToolCall] = []
        for index, tc in enumerate(tool_calls):
            base = str(tc.id or f"call_{index}")
            candidate = base
            suffix = 0
            while candidate in used:
                suffix += 1
                candidate = f"{base}_i{iteration}_{suffix}"
            used.add(candidate)
            result.append(LLMToolCall(
                id=candidate,
                name=tc.name,
                arguments=dict(tc.arguments or {}),
            ))
        return result

    async def _call_llm(
        self,
        messages: List[LLMMessage],
        ctx: StatelessContext,
    ) -> Optional[LLMResponse]:
        """Call LLM with tools and streaming support.

        Wraps the synchronous LLM call with asyncio.wait_for + asyncio.to_thread
        to guarantee a hard timeout and prevent event-loop blocking.
        """
        try:
            system_prompt, stream_scope, stream_to_user = self._llm_call_mode(messages, ctx)
            tools_for_call = (
                [] if self._is_response_only(messages) else self._cached_tools
            )
            if self._llm_invoke is not None:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._llm_invoke,
                        system=system_prompt,
                        user=self._messages_to_user_text(messages),
                        temperature=0.2,
                        timeout=120,
                        tools=tools_for_call,
                        workspace_id=ctx.workspace_id,
                        session_id=ctx.session_id,
                        extra={
                            "runtime_engine": "ssot_runtime",
                            "stream_scope": stream_scope,
                            "stream_to_user": stream_to_user,
                            "workspace_id": ctx.workspace_id,
                            "session_id": ctx.session_id,
                        },
                    ),
                    timeout=300,
                )
                return self._coerce_llm_response(raw)
        except asyncio.TimeoutError:
            self._llm_call_count += 1
            return LLMResponse(error="llm_call_timeout")
        except Exception as e:
            self._llm_call_count += 1  # P1-7: count against budget even on error
            return LLMResponse(error=str(e))

    @staticmethod
    def _llm_call_mode(
        messages: List[LLMMessage],
        ctx: StatelessContext,
    ) -> tuple[str, str, bool]:
        response_only = any(
            message.role == "user"
            and RESPONSE_ONLY_MARKER in str(message.content or "")
            for message in messages[-2:]
        )
        if response_only:
            return build_runtime_system_prompt(ctx.extras), "response", True

        has_tool_context = any(
            m.role == "tool"
            or (m.role == "user" and "AUTO TRACKING RESULTS" in str(m.content or ""))
            for m in messages
        )
        if has_tool_context:
            # A tool result is evidence for the next reasoning step, not proof
            # that the workflow is complete. Keep the full execution contract so
            # the model can issue dependent calls, recover from validation
            # errors, or finish naturally. Only an explicit marker above enters
            # the tool-free response mode.
            return build_runtime_system_prompt(ctx.extras), "continuation", True
        return build_runtime_system_prompt(ctx.extras), "planner", False

    @staticmethod
    def _is_response_only(messages: List[LLMMessage]) -> bool:
        return any(
            message.role == "user"
            and RESPONSE_ONLY_MARKER in str(message.content or "")
            for message in messages[-2:]
        )

    @staticmethod
    def _has_complete_analysis_artifact(
        results: List[StreamingToolResult],
    ) -> bool:
        return any(
            result.ok
            and result.tool_name.replace("__", ".") == "workspace.artifact"
            and result.output.get("content_complete") is True
            and result.output.get("artifact_type") in {
                "input_data", "output_data", "report",
            }
            for result in results
        )

    def _messages_to_user_text(self, messages: List[LLMMessage]) -> str:
        """Serialize loop messages for injected LLM adapters.

        The production adapter accepts ``system`` + ``user`` strings, while
        QueryLoop internally keeps OpenAI-style tool messages. This projection
        preserves the relevant context without bypassing the injected adapter.
        """
        parts: list[str] = []
        response_only = self._is_response_only(messages)
        for m in messages:
            if m.role == "system":
                continue
            label = m.role.upper()
            content = m.content
            if m.tool_calls and not response_only:
                parts.append(
                    f"{label} TOOL_CALLS: "
                    f"{json.dumps(m.tool_calls, ensure_ascii=False, default=str)}"
                )
            if content:
                parts.append(f"{label}: {content}")
            if m.tool_call_id:
                if parts:
                    parts[-1] = f"{parts[-1]} (tool_call_id={m.tool_call_id})"  # P2-3: simpler than slice assignment
        return "\n\n".join(parts)

    _TIMEOUT_TRUNCATION_MARKER = "\n\n⚠️ [模型响应超时，以上为已接收的部分内容]"
    _LENGTH_TRUNCATION_MARKER = "\n\n⚠️ [回复达到输出长度上限，以上内容可能不完整]"

    def _coerce_llm_response(self, raw: Any) -> LLMResponse:
        """Coerce injected adapter output into QueryLoop's LLMResponse shape.
        
        Also strips ``<think>...</think>`` tags that some models (MiniMax-M3)
        leak into visible output — they confuse final_response_summary truncation
        and make users think the model is talking to itself.
        """
        if isinstance(raw, LLMResponse):
            raw.content = self._strip_think_tags(str(raw.content or ""))
            reason = str(raw.finish_reason or "").lower()
            if reason == "stream_truncated" and raw.content:
                raw.content = raw.content.rstrip() + self._TIMEOUT_TRUNCATION_MARKER
                raw.metadata = {**(raw.metadata or {}), "output_truncated": True, "truncation_reason": "timeout"}
            elif reason in {"length", "max_tokens", "content_length"} and raw.content:
                raw.content = raw.content.rstrip() + self._LENGTH_TRUNCATION_MARKER
                raw.metadata = {**(raw.metadata or {}), "output_truncated": True, "truncation_reason": "length"}
            if not raw.tool_calls:
                parsed = self._response_from_plan_text(raw.content)
                if parsed is not None:
                    parsed.provider = raw.provider
                    parsed.model = raw.model
                    parsed.usage = raw.usage
                    parsed.finish_reason = raw.finish_reason
                    parsed.raw = raw.raw
                    parsed.error = raw.error
                    parsed.metadata = dict(raw.metadata or {})
                    return parsed
            return raw
        if raw is None:
            return LLMResponse(error="empty_llm_response")
        tool_calls = getattr(raw, "tool_calls", None)
        if tool_calls is not None:
            return LLMResponse(
                content=self._strip_think_tags(str(getattr(raw, "content", "") or "")),
                error=getattr(raw, "error", None),
                tool_calls=list(tool_calls or []),
            )
        text = self._strip_think_tags(str(raw))
        parsed = self._response_from_plan_text(text)
        if parsed is not None:
            return parsed
        return LLMResponse(content=text)

    def _response_from_plan_text(self, text: str) -> LLMResponse | None:
        data = self._try_parse_json_object(text)
        if data is not None:
            nodes = data.get("nodes")
            if isinstance(nodes, list):
                calls: list[LLMToolCall] = []
                for idx, node in enumerate(nodes):
                    if not isinstance(node, dict):
                        continue
                    tool = str(node.get("tool") or "").strip()
                    if not tool:
                        continue
                    calls.append(LLMToolCall(
                        id=str(node.get("id") or f"call_{idx}"),
                        name=tool,
                        arguments=dict(node.get("args") or {}),
                    ))
                return LLMResponse(
                    content=self._strip_think_tags(str(data.get("final_response") or "")),
                    tool_calls=calls,
                )
        return None
    
    @staticmethod
    def _strip_think_tags(text: str) -> str:
        """Remove ``<think>...</think>`` blocks from LLM output.
        
        Some models (MiniMax-M3) emit chain-of-thought reasoning inside XML
        tags. We strip the tags and their content before passing the text on.
        """
        import re
        return re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL).strip()

    @staticmethod
    def _try_parse_json_object(text: str) -> dict[str, Any] | None:
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            data = json.loads(cleaned)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _parse_tool_calls(self, raw: List[LLMToolCall]) -> List[LLMToolCall]:
        """Normalise raw tool calls from LLM response (may be dict or LLMToolCall)."""
        result = []
        for tc in raw:
            if isinstance(tc, dict):
                # Raw dict from provider
                args = tc.get("arguments", {})
                tid = tc.get("id", "")
                tname = tc.get("name", "")
            else:
                # LLMToolCall dataclass
                args = getattr(tc, "arguments", {})
                tid = getattr(tc, "id", "")
                tname = getattr(tc, "name", "")
            
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            
            # Normalise double-underscore to dots
            tname = tname.replace("__", ".")
            if not tid:
                tid = f"call_{len(result)}"
            
            result.append(LLMToolCall(
                id=str(tid),
                name=tname,
                arguments=args,
            ))
        return result

    @staticmethod
    def _tool_call_key(tc: LLMToolCall) -> str:
        return (
            f"{tc.name}:"
            f"{json.dumps(tc.arguments or {}, sort_keys=True, ensure_ascii=False, default=str)}"
        )

    def _prepare_tool_calls(
        self,
        ctx: StatelessContext,
        tool_calls: List[LLMToolCall],
    ) -> dict[str, Any]:
        """Run QueryLoop's pre-execution hard boundaries.

        QueryLoop is the execution path. It still keeps semantic repair, risk,
        and approval boundaries directly on the current call batch.
        """
        nodes = self._tool_calls_to_nodes(tool_calls)
        from .semantic_validator import SemanticValidator
        from .pre_execution_repair import (
            PreExecutionRepairEngine,
            REPAIRABLE_ERROR_CODES,
        )
        from .risk_policy import RiskPolicyEngine
        from .plan_enrichment import enrich_tool_calls_from_user_request

        enrichment_events = enrich_tool_calls_from_user_request(nodes, ctx.user_input)
        if enrichment_events:
            ctx.extras.setdefault("plan_enrichment_events", [])
            ctx.extras["plan_enrichment_events"].extend(
                asdict(event) for event in enrichment_events
            )

        validator = SemanticValidator(self._tool_registry)
        validation = validator.validate(nodes)
        if not validation.valid:
            repair = PreExecutionRepairEngine().try_repair(nodes, validation.errors)
            self._record_pre_exec_repair(ctx, repair)
            if repair.repaired and repair.repaired_nodes is not None:
                nodes = repair.repaired_nodes
                validation = validator.validate(nodes)

        if not validation.valid:
            for node in nodes:
                if any(e.node_id == node.id for e in validation.errors):
                    node.status = ExecutionStatus.SKIPPED
                    node.error = "Blocked by semantic validation"
            errors = [
                f"{e.node_id}:{e.code}:{e.message}"
                for e in validation.errors
            ]
            validation_errors = [
                {
                    "node_id": e.node_id,
                    "code": e.code,
                    "message": e.message,
                    "details": dict(getattr(e, "details", {}) or {}),
                }
                for e in validation.errors
            ]
            self._record_blocked_audit_nodes(ctx, nodes)
            # Repairable semantic errors remain recoverable by the LLM when
            # deterministic repair could not resolve them. The repair engine
            # owns this code set so validation and retry cannot drift apart.
            is_hard = any(
                e.code not in REPAIRABLE_ERROR_CODES
                for e in validation.errors
            )
            return {
                "ok": False,
                "error": "semantic_validation_failed",
                "errors": errors,
                "validation_errors": validation_errors,
                "hard_block": is_hard,
                "risk_level": "high" if is_hard else "low",
                "message": "工具调用校验失败：\n" + "\n".join(f"- {e}" for e in errors),
            }

        risk = RiskPolicyEngine(self._config).assess(nodes)
        ctx.extras.update({
            "approval_required": bool(risk.requires_approval),
            "hard_block": bool(risk.hard_block),
            "approval_reason": risk.approval_reason,
            "approval_nodes": list(risk.approval_nodes),
            "approval_details": list(risk.approval_details),
        })

        if risk.hard_block:
            for node in nodes:
                if node.id in risk.blocked_nodes:
                    node.status = ExecutionStatus.SKIPPED
                    node.error = risk.blocked_reason or "Blocked by risk policy"
            reason = risk.blocked_reason or "blocked_by_risk_policy"
            self._record_blocked_audit_nodes(ctx, nodes)
            return {
                "ok": False,
                "error": "risk_hard_block",
                "errors": [reason],
                "hard_block": True,
                "risk_level": risk.risk_level,
                "message": f"工具调用被安全策略阻断：{reason}",
            }

        approved_keys = set(ctx.extras.get("approved_tool_call_keys") or [])
        approval_nodes = [node for node in nodes if node.id in risk.approval_nodes]
        approval_satisfied = bool(approval_nodes) and all(
            self._tool_call_key(LLMToolCall(
                id=node.id,
                name=node.tool,
                arguments=dict(node.args or {}),
            )) in approved_keys
            for node in approval_nodes
        )
        if risk.requires_approval and not approval_satisfied:
            return {
                "ok": False,
                "error": "approval_required",
                "errors": [],
                "approval_required": True,
                "approval_nodes": list(risk.approval_nodes),
                "approval_details": list(risk.approval_details),
                "risk_level": risk.risk_level,
                "message": (
                    "该操作需要用户审批后才能继续执行。"
                    f"原因：{risk.approval_reason or 'high_risk_tool_or_command'}"
                ),
            }

        repaired_calls = [
            LLMToolCall(id=n.id, name=n.tool, arguments=dict(n.args or {}))
            for n in nodes
        ]
        return {
            "ok": True,
            "tool_calls": repaired_calls,
            "risk_level": risk.risk_level,
            "approval_required": False,
        }

    @staticmethod
    def _tool_calls_to_nodes(tool_calls: List[LLMToolCall]) -> list[ExecutionNode]:
        from .action_alias import resolve_action_alias

        nodes: list[ExecutionNode] = []
        for idx, tc in enumerate(tool_calls):
            args = dict(tc.arguments or {})
            action_original = ""
            action_normalized_from_alias = False
            raw_action = args.get("action")
            if isinstance(raw_action, str) and raw_action:
                resolution = resolve_action_alias(tc.name.replace("__", "."), raw_action)
                if resolution.matched:
                    args["action"] = resolution.canonical_action
                    if resolution.operation:
                        args["operation"] = resolution.operation
                    action_original = resolution.original_action
                    action_normalized_from_alias = True
            nodes.append(ExecutionNode(
                id=tc.id or f"call_{idx}",
                tool=tc.name.replace("__", "."),
                args=args,
                action_original=action_original,
                action_normalized_from_alias=action_normalized_from_alias,
            ))
        return nodes

    @staticmethod
    def _record_blocked_audit_nodes(ctx: StatelessContext, nodes: list[ExecutionNode]) -> None:
        blocked = []
        for node in nodes:
            if node.status != ExecutionStatus.SKIPPED:
                continue
            blocked.append({
                "node_id": node.id,
                "tool": node.tool,
                "args": dict(node.args or {}),
                "status": node.status.value,
                "latency_ms": node.latency_ms,
                "error": node.error or "blocked",
            })
        if blocked:
            ctx.extras["audit_blocked_nodes"] = blocked

    @staticmethod
    def _record_pre_exec_repair(ctx: StatelessContext, repair) -> None:
        events = []
        for event in getattr(repair, "repair_events", []) or []:
            try:
                events.append(asdict(event))
            except Exception:
                events.append(dict(getattr(event, "__dict__", {}) or {}))
        if events:
            ctx.extras["pre_exec_repair_events"] = events
        ctx.extras["pre_exec_repair_applied"] = bool(getattr(repair, "repaired", False))

    def _append_turn_nudge(
        self,
        messages: List[LLMMessage],
        nudge_text: str,
    ) -> List[LLMMessage]:
        """Append a user nudge to guide the LLM toward a final answer.

        Used when the LLM returns empty text after tools have produced
        results, then nudge the same runtime loop to produce the response
        to produce the answer directly.
        """
        new_msgs = list(messages)
        new_msgs.append(LLMMessage(role="user", content=nudge_text))
        return new_msgs

    @staticmethod
    def _build_tool_failure_recovery_nudge(
        failed_results: List[StreamingToolResult],
    ) -> str:
        """Tell the model to recover by replanning, never blind replay.

        Mechanical retries are owned by ToolRetryPolicy and only apply to
        idempotent reads. This instruction covers the separate LLM-level path:
        use existing successful evidence, change arguments/tool/strategy when
        useful, or explain a terminal blocker.
        """
        failures = []
        for result in failed_results[:6]:
            error = str(result.error or "tool returned failure").replace("\n", " ")[:240]
            failures.append(f"- {result.tool_name}: {error}")
        return (
            "[RUNTIME TOOL RECOVERY]\n"
            "One or more tool calls failed:\n"
            + "\n".join(failures)
            + "\nDo not repeat an unchanged failed call. Do not bypass security or approval policy. "
            "First use any successful evidence already in the conversation. If the requested "
            "outcome still needs work, issue a changed safe call using corrected arguments, a "
            "more appropriate tool, or a different strategy. If no safe recovery exists, answer "
            "with the concrete blocker and the best next action."
        )

    def _append_tool_round(
        self,
        messages: List[LLMMessage],
        tool_calls: List[LLMToolCall],
        results: List[StreamingToolResult],
    ) -> List[LLMMessage]:
        """Append assistant tool_calls + tool results to messages.
        
        IMPORTANT: assistant message uses __ names (LLM format), tool results
        use cross-referenced call_id to match tool definitions.
        """
        new_msgs = list(messages)

        # Assistant message with tool calls (MUST use __ names to match tool defs)
        assistant_tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": (tc.name or "").replace(".", "__"),  # dots → __ for API
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ]
        new_msgs.append(LLMMessage(
            role="assistant",
            content="",
            tool_calls=assistant_tool_calls,
        ))

        original_call_ids = {tc.id for tc in tool_calls}
        extra_results: list[StreamingToolResult] = []

        # Tool result messages for model-requested calls only. Auto-tracking
        # polls are internal and do not have matching assistant tool_calls.
        for r in results:
            if r.call_id not in original_call_ids:
                extra_results.append(r)
                continue
            # v3.11: ensure errors are visible to the LLM even when r.output is empty
            tool_payload = dict(r.output) if r.output else {}
            if not tool_payload.get("ok", True) and r.error and not tool_payload.get("errors"):
                tool_payload["errors"] = [r.error]
            if tool_payload.get("ok", True) and r.error:
                tool_payload["ok"] = False
                tool_payload["errors"] = [r.error]
            is_complete_text_artifact = (
                r.tool_name.replace("__", ".") == "workspace.artifact"
                and tool_payload.get("content_complete") is True
                and tool_payload.get("artifact_type") in {
                    "input_data", "output_data", "report",
                }
            )
            output_str = (
                _artifact_analysis_content(
                    tool_payload,
                    max_chars=min(
                        ARTIFACT_ANALYSIS_MAX_CHARS,
                        self._context_budget.artifact_result_tokens * 2,
                    ),
                )
                if is_complete_text_artifact
                else _json_compact(
                    tool_payload,
                    max_chars=min(
                        TOOL_MESSAGE_MAX_CHARS,
                        self._context_budget.per_tool_result_tokens * 2,
                    ),
                )
            )
            new_msgs.append(LLMMessage(
                role="tool",
                content=output_str,
                tool_call_id=r.call_id,
            ))

        if extra_results:
            payload = [
                {
                    "tool": r.tool_name,
                    "tool_id": r.tool_name,
                    "call_id": r.call_id,
                    "ok": r.ok,
                    "error": r.error,
                    "output": r.output,
                }
                for r in extra_results
            ]
            output_str = _json_compact(
                payload,
                max_chars=min(
                    TOOL_MESSAGE_MAX_CHARS,
                    self._context_budget.per_tool_result_tokens * 2,
                ),
            )
            new_msgs.append(LLMMessage(
                role="user",
                content="AUTO TRACKING RESULTS:\n" + output_str,
            ))

        return new_msgs

    # ── Tracking / Polling ──────────────────────────────────────────────

    async def _settle_tracking(
        self,
        ctx: StatelessContext,
        results: List[StreamingToolResult],
        budget=None,
    ) -> List[StreamingToolResult]:
        """After tool execution, auto-poll long tasks.

        Polling is generic and bounded. It runs only when the tool producer
        declares a non-terminal ``long_task`` tracking payload.
        Uses the tool's canonical name for get calls.
        """
        # Keep every poll in tracking_events for diagnostics, but expose only
        # the latest observation for each source call to the model/result
        # projection. Replaying dozens of intermediate "running" rows bloats
        # context and makes internal polling look like business tool work.
        latest_by_source: dict[str, StreamingToolResult] = {}
        if not getattr(self._config, "tracking_enabled", True):
            return []

        max_polls = max(0, int(getattr(self._config, "tracking_max_polls", 8) or 0))
        cap_seconds = float(getattr(self._config, "tracking_poll_interval_cap_seconds", 2.0))
        max_seconds = max(0, float(getattr(self._config, "tracking_max_seconds", 60)))
        if max_polls <= 0:
            return []

        deadline = time.monotonic() + max_seconds
        user_input = ctx.user_input or ""
        states: list[dict[str, Any]] = []

        for r in results:
            tracking = extract_tracking_payload(r.output)
            if not tracking:
                continue
            tracking = normalize_tracking_payload(tracking)

            if tracking.get("done"):
                continue

            # Producer-declared tracking avoids keyword or intent guessing.
            if not self._should_poll_tracking(user_input, tracking):
                continue

            task_id = str(tracking.get("task_id") or "").strip()
            # Use the canonical tool name from result, not domain from tracking
            tool_name = (r.tool_name or "").strip()
            if not task_id or not tool_name:
                continue
            if not self._tool_runtime.has_tool(tool_name):
                continue

            ctx.extras.setdefault("tracking_events", [])
            ctx.extras["tracking_events"].append({
                "tool": tool_name,
                "call_id": r.call_id,
                "tracking": tracking,
                "source": "initial",
            })
            ctx.extras["tracking_summary"] = tracking

            states.append({
                "result": r,
                "tracking": tracking,
                "task_id": task_id,
                "tool_name": tool_name,
                "poll_index": 0,
                "last_error_count": 0,
                "due_at": time.monotonic() + self._tracking_wait(
                    tracking, cap_seconds, deadline
                ),
            })

        state_by_source = {
            state["result"].call_id: state
            for state in states
        }

        # Poll the earliest-due task first, then requeue it. This preserves one
        # global tracking deadline while preventing the first long task from
        # consuming the entire window and starving the rest.
        while states and time.monotonic() < deadline and not self._is_cancelled(ctx):
            states = [
                state for state in states
                if not state["tracking"].get("done")
                and int(state["poll_index"]) < max_polls
                and int(state["last_error_count"]) < 3
            ]
            if not states:
                break
            state = min(states, key=lambda item: float(item["due_at"]))
            wait_s = min(
                max(0.0, float(state["due_at"]) - time.monotonic()),
                max(0.0, deadline - time.monotonic()),
            )
            if wait_s > 0 and await self._sleep_until_poll_or_cancel(ctx, wait_s):
                break

            state["poll_index"] = int(state["poll_index"]) + 1
            poll_index = int(state["poll_index"])
            source_result = state["result"]
            tracking = state["tracking"]
            tool_name = str(state["tool_name"])
            task_id = str(state["task_id"])
            poll_call_id = f"{source_result.call_id}_track_{poll_index}"
            poll_arguments = dict(tracking.get("poll_arguments") or {})
            poll_arguments.setdefault("task_id", task_id)
            poll_arguments.setdefault("action", str(tracking.get("poll_action") or "get"))
            poll_call = LLMToolCall(
                id=poll_call_id,
                name=tool_name,
                arguments=poll_arguments,
            )
            try:
                poll_result = await self._executor._execute_one(
                    poll_call, ctx=ctx, budget=budget
                )
                latest_by_source[source_result.call_id] = poll_result

                new_tracking = extract_tracking_payload(poll_result.output)
                if new_tracking:
                    tracking = normalize_tracking_payload(new_tracking)
                    state["tracking"] = tracking
                    ctx.extras["tracking_summary"] = tracking
                    ctx.extras["tracking_events"].append({
                        "tool": tool_name,
                        "call_id": poll_call_id,
                        "tracking": tracking,
                        "source": "poll",
                        "poll_index": poll_index,
                    })
                if poll_result.ok:
                    state["last_error_count"] = 0
                else:
                    state["last_error_count"] = int(state["last_error_count"]) + 1
                state["due_at"] = time.monotonic() + self._tracking_wait(
                    state["tracking"], cap_seconds, deadline
                )
            except Exception as e:
                latest_by_source[source_result.call_id] = StreamingToolResult(
                    tool_name=tool_name,
                    call_id=poll_call_id,
                    output={},
                    ok=False,
                    error=f"poll_crash: {str(e)[:200]}",
                )
                state["last_error_count"] = 3

        for source_call_id, state in state_by_source.items():
            latest = latest_by_source.get(source_call_id)
            if latest and isinstance(latest.output, dict):
                latest.output.setdefault("tracking_poll_count", int(state["poll_index"]))
        return list(latest_by_source.values())

    async def _sleep_until_poll_or_cancel(
        self,
        ctx: StatelessContext,
        seconds: float,
    ) -> bool:
        """Sleep in short slices so a user stop is observed promptly."""
        wake_at = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < wake_at:
            if self._is_cancelled(ctx):
                return True
            await asyncio.sleep(min(0.25, max(0.0, wake_at - time.monotonic())))
        return self._is_cancelled(ctx)

    @staticmethod
    def _is_cancelled(ctx: StatelessContext) -> bool:
        check = ctx.extras.get("cancel_check")
        if not callable(check):
            return False
        try:
            return bool(check())
        except Exception:
            return False

    def _tracking_wait(self, tracking: dict, cap: float, deadline: float) -> float:
        """Calculate poll wait time, capped and bounded by deadline."""
        try:
            requested = float(tracking.get("next_poll_seconds") or 0)
        except (TypeError, ValueError):
            requested = 0.0
        remaining = max(0.0, deadline - time.monotonic())
        cap = max(0.0, cap)
        if requested <= 0 or cap <= 0 or remaining <= 0:
            return 0.0
        return max(0.0, min(requested, cap, remaining))

    def _build_tool_result_fallback(
        self,
        ctx: StatelessContext,
        results: List[StreamingToolResult],
    ) -> str:
        """Build a useful final answer when the LLM returns empty text.
        Produces a human-readable report, not raw JSON dumps.
        """
        lines: list[str] = []
        ok_count = 0
        warn_count = 0
        fail_count = 0

        for r in results:
            output = r.output if isinstance(r.output, dict) else {}
            exit_code = output.get("exit_code")

            # Classify by exit_code for exec.run tools
            if not r.ok:
                fail_count += 1
            elif exit_code is not None and exit_code != 0:
                warn_count += 1
            else:
                ok_count += 1

        if results and all(r.tool_name.replace("__", ".") == "web.manage" for r in results):
            lines.append("联网处理结果：")
        else:
            lines.append(f"工具调用：成功 {ok_count} 个" +
                         (f"，警告 {warn_count} 个" if warn_count else "") +
                         f"，失败 {fail_count} 个")

        for r in results:
            output = r.output if isinstance(r.output, dict) else {}
            exit_code = output.get("exit_code")
            ec_mark = "⚠️ " if (r.ok and exit_code is not None and exit_code != 0) else ""
            status_mark = "❌" if not r.ok else (ec_mark or "✅")

            if r.tool_name.replace("__", ".") == "web.manage":
                web_summary = self._build_web_result_fallback_line(r, output)
                if web_summary:
                    lines.append(web_summary)
                    continue

            lines.append(f"\n### {status_mark} {r.tool_name}")

            # ── exec.run: show command, exit_code, stdout, stderr ──
            if r.tool_name in ("exec.run", "exec__run", "exec__background"):
                desc = output.get("description") or output.get("command", "")
                if desc:
                    lines.append(f"> `{str(desc)[:120]}`")
                if exit_code is not None:
                    ec_str = f"exit_code={exit_code}"
                    if exit_code != 0:
                        lines.append(f"Exit code: **{ec_str}**")
                    else:
                        lines.append(f"Exit: {ec_str}")
                stdout = output.get("stdout", "")
                stderr = output.get("stderr", "")
                if stdout.strip():
                    lines.append(f"```\n{str(stdout)[:800]}\n```")
                if stderr.strip():
                    lines.append(f"```\n{str(stderr)[:800]}\n```")

            # ── other tools: compact summary ──
            else:
                summary = str(output.get("summary") or output.get("message") or "")
                if summary:
                    lines.append(summary[:8000] + ("..." if len(summary) > 8000 else ""))
                elif not r.ok:
                    lines.append(f"error: {r.error}")

            # Error message if any
            if r.error:
                hint = self._canonical_tool_hint(r.tool_name)
                if hint:
                    lines.append(f"错误: `{r.tool_name}` 不存在: {r.error}；应使用 `{hint}`")
                else:
                    lines.append(f"错误: `{r.tool_name}` 调用失败: {r.error}")

        # Tracking info
        tracking_items: list[dict[str, Any]] = []
        for r in results:
            tracking = extract_tracking_payload(r.output)
            if tracking:
                tracking_items.append(normalize_tracking_payload(tracking))

        if tracking_items:
            lines.append("")
            latest = tracking_items[-1]
            task_id = latest.get("task_id") or ""
            status = latest.get("status") or "unknown"
            done = bool(latest.get("done"))
            progress = latest.get("progress") or {}
            completed = progress.get("completed")
            total = progress.get("total")
            lines.append(f"跟踪任务 `{task_id}`：{status}，{'已完成' if done else '进行中'}")
            if completed is not None and total is not None:
                lines.append(f"进度：{completed}/{total}")
            report_url = (
                latest.get("report_url")
                or latest.get("html_url")
                or latest.get("artifact_url")
            )
            if report_url:
                lines.append(f"报告链接：{report_url}")

        return "\n".join(lines)

    def _build_web_result_fallback_line(self, result: StreamingToolResult, output: dict[str, Any]) -> str:
        payload = output.get("output") if isinstance(output.get("output"), dict) else output
        provider = str(payload.get("provider") or "")
        status = str(payload.get("status") or "")
        summary = str(payload.get("summary") or result.error or "")
        web_results = payload.get("results") if isinstance(payload.get("results"), list) else []

        if result.ok and web_results:
            lines = ["\n### 🌐 联网结果"]
            if status == "partial" or provider == "curated_official_fallback":
                lines.append("搜索引擎暂时不可用，我先拿到了可继续读取的官方来源候选；这还不是完整搜索摘要。")
            else:
                lines.append(f"已拿到 {len(web_results)} 条网页结果。")
            for item in web_results[:5]:
                title = str(item.get("title") or "网页结果")
                url = str(item.get("url") or "")
                snippet = str(item.get("snippet") or "")
                line = f"- {title}"
                if url:
                    line += f"：{url}"
                if snippet:
                    line += f" — {snippet[:220]}"
                lines.append(line)
            hint = str(payload.get("answer_hint") or "")
            if hint:
                lines.append(f"\n说明：{hint}")
            return "\n".join(lines)

        if not result.ok:
            lines = ["\n### 🌐 联网搜索未完成"]
            if summary:
                lines.append(summary)
            lines.append("当前失败发生在搜索服务侧，不代表服务器完全无法访问互联网；可以稍后重试，或改用更具体的官方 URL 让我直接读取。")
            return "\n".join(lines)

        return ""

    def _canonical_tool_hint(self, tool_name: str) -> str:
        """Suggest the canonical tool id for a category-like hallucination.

        This is a hint only; it does not execute aliases or widen the public
        tool namespace.
        """
        name = (tool_name or "").strip()
        if not name or self._tool_runtime.has_tool(name):
            return ""
        prefix = name + "."
        matches = sorted(t for t in self._tool_registry if t.startswith(prefix))
        return matches[0] if len(matches) == 1 else ""

    # ── Private helpers ──────────────────────────────────────────────────
