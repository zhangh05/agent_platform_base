"""Protocol-safe context compaction for the SSOT QueryLoop.

Compacted history is untrusted evidence.  It must never become a system
message, and an assistant tool-call envelope is either retained intact with
its matching results or omitted as one group.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from html import escape, unescape
from typing import Any, Iterable

from agent.llm.schemas import LLMMessage
from storage.redaction import redact_text, redact_value

from .context_budget import (
    estimate_json_tokens,
    estimate_text_tokens,
    project_json_to_tokens,
    truncate_text_to_tokens,
)


DEFAULT_COMPACT_MESSAGE_TOKENS = 24_000
COMPACTED_HISTORY_TAG = "compacted_history"
_PRIORITY_OUTPUT_KEYS = (
    "ok", "status", "task_id", "task", "tracking", "progress", "done",
    "report_url", "html_url", "artifact_url", "url", "count", "total",
    "success", "failed", "skipped", "summary", "message", "error",
    "reason", "title", "name", "format",
)
_HISTORY_SIGNAL_PATTERNS = (
    ("constraint", r"必须|不得|不能|不允许|不要|只能|至少|最多|务必|must\b|never\b|only\b|required\b"),
    ("decision", r"决定|确定|采用|改成|取消|保留|删除|结论|方案|decid|choose|remove|retain"),
    ("status", r"完成|成功|失败|错误|异常|阻塞|待处理|运行中|已修复|complete|failed|error|blocked|pending"),
    ("artifact", r"文件|附件|报告|制品|知识库|数据|配置|日志|file|artifact|report|config|log"),
    ("entity", r"(?:\b\d{1,3}(?:\.\d{1,3}){3}\b)|(?:\b(?:vlan|task|run|file|session)[-_:# ]?[a-z0-9_.-]+\b)|(?:\b[a-z]+\d+(?:/\d+){1,3}\b)|(?:[A-Za-z0-9_.-]+\.(?:json|ya?ml|log|txt|md|pdf|docx|xlsx))"),
    ("correction", r"不是|不对|更正|纠正|实际以|以.+为准|instead|correction|actually"),
)
_HISTORY_SIGNAL_WEIGHTS = {
    "constraint": 5,
    "correction": 5,
    "decision": 4,
    "status": 3,
    "entity": 2,
    "artifact": 1,
}
_HISTORY_ENTITY_PATTERN = re.compile(
    r"\b\d{1,3}(?:\.\d{1,3}){3}\b|"
    r"\b(?:vlan|task|run|file|session)[-_:# ]?[a-z0-9_.-]+\b|"
    r"\b[a-z]+\d+(?:/\d+){1,3}\b|"
    r"[A-Za-z0-9_.-]+\.(?:json|ya?ml|log|txt|md|pdf|docx|xlsx)",
    flags=re.IGNORECASE,
)


@dataclass
class CompactInfo:
    """Auditable projection of one runtime compaction event."""

    compacted: bool = False
    before_chars: int = 0
    after_chars: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    removed: int = 0
    saved_chars: int = 0
    tools_used: list[str] = field(default_factory=list)
    tool_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    key_hints: list[str] = field(default_factory=list)
    source_kind: str = "conversation_history"
    trust: str = "untrusted_data"
    redaction_applied: bool = False
    truncation_reason: str = ""


def history_state_signals(text: str) -> tuple[str, ...]:
    value = str(text or "")
    return tuple(
        label
        for label, pattern in _HISTORY_SIGNAL_PATTERNS
        if re.search(pattern, value, flags=re.IGNORECASE)
    )


def history_importance_score(text: str) -> int:
    return sum(_HISTORY_SIGNAL_WEIGHTS[signal] for signal in history_state_signals(text))


def build_history_state_record(
    role: str,
    content: str,
    *,
    tool_context: Iterable[dict[str, Any]] = (),
    references: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build the durable minimal state used before raw historical prose."""
    safe_content = redact_text(str(content or ""))
    signals = history_state_signals(safe_content)
    entities = list(dict.fromkeys(match.group(0)[:160] for match in _HISTORY_ENTITY_PATTERN.finditer(safe_content)))[:16]
    constraints: list[str] = []
    for line in re.split(r"[\r\n]+|(?<=[。！？.!?])", safe_content):
        if "constraint" in history_state_signals(line) or "correction" in history_state_signals(line):
            compact = line.strip()[:500]
            if compact and compact not in constraints:
                constraints.append(compact)
        if len(constraints) >= 8:
            break
    tools = []
    unresolved = []
    for item in tool_context:
        if not isinstance(item, dict):
            continue
        fact = {
            "tool_id": str(item.get("tool_id") or "tool")[:120],
            "ok": bool(item.get("ok", False)),
            "summary": _safe_fact(item.get("summary") or "", 300),
        }
        tools.append(fact)
        if not fact["ok"]:
            unresolved.append(fact)
    reference_keys = (
        "file_id", "artifact_id", "task_id", "run_id", "name", "filename",
        "title", "mime_type", "artifact_type", "size_bytes",
    )
    refs = [
        redact_value({key: item[key] for key in reference_keys if item.get(key) not in (None, "")})
        for item in references
        if isinstance(item, dict)
    ][:16]
    refs = [item for item in refs if item]
    return {
        "schema": "runtime.history_state.v1",
        "role": role if role in {"user", "assistant"} else "assistant",
        "signals": list(signals),
        "entities": entities,
        "constraints": constraints,
        "tool_facts": tools[:8],
        "unresolved": unresolved[:8],
        "references": refs,
    }


def estimate_chars(messages: Iterable[LLMMessage]) -> int:
    total = 0
    for message in messages:
        if isinstance(message.content, list):
            total += len(json.dumps(message.content, ensure_ascii=False, default=str))
        else:
            total += len(str(message.content or ""))
        if message.tool_calls:
            total += len(json.dumps(message.tool_calls, ensure_ascii=False, default=str))
    return total


def estimate_message_tokens(messages: Iterable[LLMMessage]) -> int:
    total = 0
    for message in messages:
        total += 4
        total += (
            estimate_json_tokens(message.content)
            if isinstance(message.content, list)
            else estimate_text_tokens(message.content)
        )
        if message.tool_calls:
            total += estimate_json_tokens(message.tool_calls)
        if message.tool_call_id:
            total += estimate_text_tokens(message.tool_call_id) + 2
    return total


def message_groups(messages: list[LLMMessage]) -> list[list[LLMMessage]]:
    """Keep every assistant tool-call envelope with all matching results."""
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


def _is_compaction_record(message: LLMMessage) -> bool:
    return (
        message.role == "user"
        and isinstance(message.content, str)
        and message.content.lstrip().startswith(f"<{COMPACTED_HISTORY_TAG} ")
    )


def _safe_fact(value: Any, limit: int = 240) -> str:
    redacted = redact_value(value)
    if isinstance(redacted, (dict, list)):
        text = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"), default=str)
    else:
        text = str(redacted)
    text = redact_text(text).replace("\x00", "").replace("\n", " ").strip()
    return text[:limit]


def _collect_record(messages: list[LLMMessage]) -> tuple[dict[str, Any], list[str], dict[str, dict[str, int]], list[str]]:
    tools: list[str] = []
    stats: dict[str, dict[str, int]] = {}
    call_names: dict[str, str] = {}
    facts: list[dict[str, str]] = []
    hints: list[str] = []
    prior_message_count = 0
    for message in messages:
        prior = _parse_compaction_record(message)
        if not prior:
            continue
        prior_message_count += int(prior.get("original_message_count", 0) or 0)
        for name, raw_stats in (prior.get("tool_usage") or {}).items():
            if not isinstance(raw_stats, dict):
                continue
            tool_name = str(name)[:160]
            if tool_name not in tools:
                tools.append(tool_name)
            current = stats.setdefault(tool_name, {"ok": 0, "failed": 0, "total": 0})
            for key in ("ok", "failed", "total"):
                current[key] += int(raw_stats.get(key, 0) or 0)
        for fact in prior.get("retained_facts") or []:
            if isinstance(fact, dict) and fact not in facts and len(facts) < 16:
                facts.append(redact_value(fact))
    for message in messages:
        for call in message.tool_calls or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = str(call.get("name") or function.get("name") or "")[:160]
            if not name:
                continue
            if name not in tools:
                tools.append(name)
            stats.setdefault(name, {"ok": 0, "failed": 0, "total": 0})["total"] += 1
            if call.get("id"):
                call_names[str(call["id"])] = name

    for message in messages:
        if message.role != "tool":
            continue
        try:
            payload = json.loads(str(message.content or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {"summary": str(message.content or "")}
        if not isinstance(payload, dict):
            payload = {"summary": payload}
        payload = redact_value(payload)
        tool_name = call_names.get(str(message.tool_call_id or ""), "tool")
        if tool_name in stats:
            stats[tool_name]["ok" if payload.get("ok", True) else "failed"] += 1
        for key in _PRIORITY_OUTPUT_KEYS:
            if key not in payload or payload[key] in (None, "", [], {}):
                continue
            fact = {"source_tool": tool_name, "key": key, "value": _safe_fact(payload[key])}
            if fact not in facts and len(facts) < 16:
                facts.append(fact)
            if key in {"summary", "message", "error", "reason"} and len(hints) < 8:
                hint = f"{key}={fact['value']}"
                if hint not in hints:
                    hints.append(hint)

    record = {
        "schema": "runtime.compacted_history.v1",
        "source_kind": "conversation_history",
        "trust": "untrusted_data",
        "redaction_applied": True,
        "truncation_reason": "context_budget",
        "original_message_count": prior_message_count + sum(
            1 for message in messages if not _is_compaction_record(message)
        ),
        "tool_usage": stats,
        "retained_facts": facts,
    }
    return record, tools, stats, hints


def _parse_compaction_record(message: LLMMessage) -> dict[str, Any]:
    if not _is_compaction_record(message):
        return {}
    content = str(message.content or "")
    try:
        payload = content.split("\n", 1)[1].rsplit("\n", 1)[0]
        parsed = json.loads(unescape(payload))
    except (IndexError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return redact_value(parsed) if isinstance(parsed, dict) else {}


def _record_message(record: dict[str, Any], max_tokens: int) -> LLMMessage:
    projected, _ = project_json_to_tokens(redact_value(record), max(96, max_tokens - 40))
    payload = json.dumps(projected, ensure_ascii=False, separators=(",", ":"), default=str)
    # Escaping is mandatory: tool data must not close or forge the boundary.
    content = (
        f'<{COMPACTED_HISTORY_TAG} data_only="true" trust="untrusted_data" '
        'source_kind="conversation_history">\n'
        + escape(payload, quote=False)
        + f"\n</{COMPACTED_HISTORY_TAG}>"
    )
    return LLMMessage(role="user", content=content)


def _fit_plain_message(message: LLMMessage, max_tokens: int) -> LLMMessage:
    """Fit plain content; tool-call arguments are immutable protocol data."""
    if estimate_message_tokens([message]) <= max_tokens:
        return copy.deepcopy(message)
    cloned = copy.deepcopy(message)
    if cloned.tool_calls:
        # Arguments must remain byte-for-byte valid JSON. Only optional prose
        # next to the call may be shortened; the whole group is dropped later
        # if the immutable envelope still cannot fit.
        if isinstance(cloned.content, str) and cloned.content:
            cloned.content = truncate_text_to_tokens(cloned.content, max(8, max_tokens // 5))[0]
        return cloned
    if cloned.role == "tool":
        try:
            value = json.loads(str(cloned.content or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {"summary": redact_text(str(cloned.content or ""))}
        projected, _ = project_json_to_tokens(redact_value(value), max(64, max_tokens - 8))
        cloned.content = json.dumps(projected, ensure_ascii=False, separators=(",", ":"), default=str)
    elif isinstance(cloned.content, str):
        cloned.content = truncate_text_to_tokens(cloned.content, max(8, max_tokens - 8))[0]
    else:
        cloned.content = truncate_text_to_tokens(
            json.dumps(cloned.content, ensure_ascii=False, default=str),
            max(8, max_tokens - 8),
        )[0]
    return cloned


def _latest_real_user(messages: list[LLMMessage]) -> LLMMessage | None:
    return next(
        (message for message in reversed(messages) if message.role == "user" and not _is_compaction_record(message)),
        None,
    )


def _within_budget(messages: list[LLMMessage], token_limit: int) -> list[LLMMessage]:
    if estimate_message_tokens(messages) <= token_limit:
        return messages
    groups = message_groups(messages)
    # Preserve the first governing system group and newest actual request.
    while len(groups) > 2:
        removable = next(
            (i for i in range(1, len(groups)) if not any(_latest_real_user(messages) is m for m in groups[i])),
            None,
        )
        if removable is None:
            break
        groups.pop(removable)
        candidate = [message for group in groups for message in group]
        if estimate_message_tokens(candidate) <= token_limit:
            return candidate

    system = next((message for message in messages if message.role == "system"), None)
    latest_user = _latest_real_user(messages)
    anchors = [message for message in (system, latest_user) if message is not None]
    if anchors:
        fitted = [_fit_plain_message(message, max(8, token_limit // len(anchors))) for message in anchors]
        if estimate_message_tokens(fitted) <= token_limit:
            return fitted
    fallback = latest_user or system
    if fallback is None:
        return []
    return [_fit_plain_message(fallback, max(8, token_limit - 4))]


def compact_messages(
    messages: list[LLMMessage],
    *,
    max_tokens: int | None = None,
) -> tuple[list[LLMMessage], CompactInfo]:
    """Validate and return the complete transcript unchanged.

    ``max_tokens`` is retained only for source compatibility.  Runtime code
    must not turn a provider capacity estimate into permission to remove part
    of the user's conversation, a tool call, or a tool result.
    """
    assert_tool_protocol(messages)
    total_chars = estimate_chars(messages)
    total_tokens = estimate_message_tokens(messages)
    return messages, CompactInfo(
        before_chars=total_chars,
        after_chars=total_chars,
        before_tokens=total_tokens,
        after_tokens=total_tokens,
    )


def assert_tool_protocol(messages: list[LLMMessage]) -> None:
    """Raise when a provider transcript contains invalid/orphaned tool data."""
    expected: set[str] = set()
    seen: set[str] = set()
    for message in messages:
        for call in message.tool_calls or []:
            if not isinstance(call, dict):
                raise ValueError("invalid tool call envelope")
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                json.loads(arguments)
            call_id = str(call.get("id") or "")
            if not call_id:
                raise ValueError("tool call missing id")
            if call_id in expected:
                raise ValueError("duplicate tool call id")
            expected.add(call_id)
        if message.role == "tool":
            call_id = str(message.tool_call_id or "")
            if not call_id or call_id not in expected:
                raise ValueError("orphaned tool result")
            if call_id in seen:
                raise ValueError("duplicate tool result")
            seen.add(call_id)
    if expected != seen:
        raise ValueError("tool call/result mismatch")
