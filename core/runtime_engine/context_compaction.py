"""Conversation integrity helpers; runtime compaction is removed."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from agent.llm.schemas import LLMMessage
from storage.redaction import redact_text, redact_value
from .context_budget import estimate_json_tokens, estimate_text_tokens

_PATTERNS = (("constraint", r"必须|不得|不能|不允许|不要|只能|至少|最多|务必|must\b|never\b|only\b|required\b"), ("decision", r"决定|确定|采用|改成|取消|保留|删除|结论|方案|decid|choose|remove|retain"), ("status", r"完成|成功|失败|错误|异常|阻塞|待处理|运行中|已修复|complete|failed|error|blocked|pending"), ("artifact", r"文件|附件|报告|制品|知识库|数据|配置|日志|file|artifact|report|config|log"), ("entity", r"\b\d{1,3}(?:\.\d{1,3}){3}\b|\b(?:vlan|task|run|file|session)[-_:# ]?[a-z0-9_.-]+\b|\b[a-z]+\d+(?:/\d+){1,3}\b|[A-Za-z0-9_.-]+\.(?:json|ya?ml|log|txt|md|pdf|docx|xlsx)"), ("correction", r"不是|不对|更正|纠正|实际以|以.+为准|instead|correction|actually"))
_WEIGHTS = {"constraint": 5, "correction": 5, "decision": 4, "status": 3, "entity": 2, "artifact": 1}

@dataclass
class CompactInfo:
    compacted: bool = False; before_chars: int = 0; after_chars: int = 0; before_tokens: int = 0; after_tokens: int = 0; removed: int = 0; saved_chars: int = 0
    tools_used: list[str] = field(default_factory=list); tool_stats: dict[str, dict[str, int]] = field(default_factory=dict); key_hints: list[str] = field(default_factory=list)
    source_kind: str = "conversation_history"; trust: str = "untrusted_data"; redaction_applied: bool = False; truncation_reason: str = ""

def history_state_signals(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _PATTERNS if re.search(pattern, str(text or ""), re.I))
def history_importance_score(text: str) -> int:
    return sum(_WEIGHTS[x] for x in history_state_signals(text))
def build_history_state_record(role: str, content: str, *, tool_context: Iterable[dict[str, Any]] = (), references: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    safe = redact_text(str(content or "")); entities = re.findall(_PATTERNS[4][1], safe, re.I)
    tools = [{"tool_id": str(x.get("tool_id") or "tool"), "ok": bool(x.get("ok", False)), "summary": redact_text(str(x.get("summary") or ""))} for x in tool_context if isinstance(x, dict)]
    reference_keys = {"file_id", "artifact_id", "task_id", "run_id", "name", "filename", "title", "mime_type", "artifact_type", "size_bytes"}
    refs = [redact_value({key: value for key, value in item.items() if key in reference_keys}) for item in references if isinstance(item, dict)]
    return {"schema":"runtime.history_state.v1", "role":role if role in {"user","assistant"} else "assistant", "signals":list(history_state_signals(safe)), "entities":list(dict.fromkeys(entities)), "constraints":[line.strip() for line in re.split(r"[\r\n]+|(?<=[。！？.!?])", safe) if "constraint" in history_state_signals(line) or "correction" in history_state_signals(line)], "tool_facts":tools, "unresolved":[x for x in tools if not x["ok"]], "references":refs}
def estimate_chars(messages: Iterable[LLMMessage]) -> int:
    return sum(len(json.dumps(x.content, ensure_ascii=False, default=str)) if isinstance(x.content,list) else len(str(x.content or "")) for x in messages)
def estimate_message_tokens(messages: Iterable[LLMMessage]) -> int:
    return sum(4 + (estimate_json_tokens(x.content) if isinstance(x.content,list) else estimate_text_tokens(x.content)) + estimate_json_tokens(x.tool_calls or []) for x in messages)
def assert_tool_protocol(messages: list[LLMMessage]) -> None:
    expected, seen = set(), set()
    for message in messages:
        for call in message.tool_calls or []:
            if not isinstance(call,dict): raise ValueError("invalid tool call envelope")
            args = (call.get("function") or {}).get("arguments")
            if isinstance(args,str): json.loads(args)
            call_id = str(call.get("id") or "")
            if call_id in expected: raise ValueError(f"duplicate tool call id: {call_id}")
            if call_id: expected.add(call_id)
        if message.role == "tool":
            call_id = str(message.tool_call_id or "")
            if not call_id or call_id not in expected: raise ValueError(f"orphaned tool result: {call_id or 'missing id'}")
            if call_id in seen: raise ValueError(f"duplicate tool result: {call_id}")
            seen.add(call_id)
def compact_messages(messages: list[LLMMessage], *, max_tokens: int | None = None) -> tuple[list[LLMMessage], CompactInfo]:
    assert_tool_protocol(messages); chars, tokens = estimate_chars(messages), estimate_message_tokens(messages)
    return messages, CompactInfo(before_chars=chars, after_chars=chars, before_tokens=tokens, after_tokens=tokens)
