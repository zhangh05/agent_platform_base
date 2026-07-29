"""Token-aware context budgeting for the SSOT runtime.

The runtime keeps the complete tool surface visible to the model. This module
accounts for that fixed cost first, then assigns the remaining input window to
messages, history, retrieval, and tool evidence.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable


DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_MAX_INPUT_TOKENS = 48_000
DEFAULT_OUTPUT_TOKENS = 4096
DEFAULT_SAFETY_TOKENS = 2048


def estimate_text_tokens(value: Any) -> int:
    """Conservative tokenizer-independent estimate for mixed Chinese/JSON text."""
    text = str(value or "")
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii_chars = len(text) - ascii_chars
    # English prose is usually near four chars/token. JSON punctuation, shell
    # commands, identifiers, and whitespace are denser, so use 3.2 here.
    return max(1, (ascii_chars + 2) // 3 + non_ascii_chars)


def estimate_json_tokens(value: Any) -> int:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        text = str(value)
    return estimate_text_tokens(text)


def truncate_text_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """Keep both ends of text while enforcing the conservative token estimate."""
    value = str(text or "")
    limit = max(1, int(max_tokens or 1))
    if estimate_text_tokens(value) <= limit:
        return value, False

    marker = "\n...[context truncated by runtime budget]...\n"
    low, high = 0, len(value)
    best = marker
    while low <= high:
        keep = (low + high) // 2
        head = int(keep * 0.75)
        tail = keep - head
        candidate = value[:head] + marker + (value[-tail:] if tail else "")
        if estimate_text_tokens(candidate) <= limit:
            best = candidate
            low = keep + 1
        else:
            high = keep - 1
    return best, True


def project_json_to_tokens(value: Any, max_tokens: int) -> tuple[Any, bool]:
    """Return a structure-preserving JSON projection within ``max_tokens``.

    This is intended for API and audit projections, not model context. Unlike
    slicing serialized JSON, it keeps dictionaries and lists parseable and
    leaves an explicit marker wherever values or collection members were
    omitted.
    """
    limit = max(64, int(max_tokens or 64))
    if estimate_json_tokens(value) <= limit:
        return copy.deepcopy(value), False
    projected = _project_json_node(value, limit, depth=0)
    return projected, True


def _project_json_node(value: Any, budget: int, *, depth: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return truncate_text_to_tokens(value, max(8, budget))[0]
    if depth >= 8:
        text, _ = truncate_text_to_tokens(
            json.dumps(value, ensure_ascii=False, default=str),
            max(8, budget - 8),
        )
        return {"_truncated": True, "preview": text}

    if isinstance(value, (list, tuple)):
        items = list(value)
        if not items:
            return []
        result: list[Any] = []
        item_budget = max(16, (budget - 24) // min(len(items), 24))
        for item in items:
            candidate = _project_json_node(item, item_budget, depth=depth + 1)
            trial = result + [candidate]
            if estimate_json_tokens(trial) > max(16, budget - 12):
                break
            result.append(candidate)
        omitted = len(items) - len(result)
        if omitted:
            marker = {"_truncated_items": omitted}
            while result and estimate_json_tokens(result + [marker]) > budget:
                result.pop()
                omitted += 1
                marker = {"_truncated_items": omitted}
            if estimate_json_tokens(result + [marker]) <= budget:
                result.append(marker)
        return result

    if isinstance(value, dict):
        entries = list(value.items())
        if not entries:
            return {}
        priority = {"ok", "status", "summary", "error", "errors", "warnings", "id", "task_id", "run_id"}
        entries.sort(key=lambda item: (str(item[0]) not in priority,))
        result: dict[str, Any] = {}
        value_budget = max(16, (budget - 32) // min(len(entries), 24))
        omitted = 0
        for key, item in entries:
            key_text = str(key)
            candidate = _project_json_node(item, value_budget, depth=depth + 1)
            trial = {**result, key_text: candidate}
            if estimate_json_tokens(trial) > max(16, budget - 16):
                omitted += 1
                continue
            result[key_text] = candidate
        omitted = len(entries) - len(result)
        if omitted:
            marker: Any = {"fields": omitted}
            while result and estimate_json_tokens({**result, "_truncated": marker}) > budget:
                last_key = next(reversed(result))
                result.pop(last_key)
                marker["fields"] += 1
            if estimate_json_tokens({**result, "_truncated": marker}) <= budget:
                result["_truncated"] = marker
        return result

    return truncate_text_to_tokens(str(value), max(8, budget))[0]


def resolve_model_context_tokens(model: str, configured: int = 0) -> int:
    if configured and configured > 0:
        return int(configured)
    name = str(model or "").lower()
    windows = (
        (("minimax-m3", "deepseek-v3", "deepseek-v4"), 512_000),
        (("claude",), 200_000),
        (("gpt-4o", "gpt-4-turbo", "qwen-max", "glm-4"), 128_000),
        (("gpt-3.5",), 16_000),
    )
    for aliases, size in windows:
        if any(alias in name for alias in aliases):
            return size
    return DEFAULT_CONTEXT_WINDOW_TOKENS


@dataclass(frozen=True)
class RuntimeContextBudget:
    context_window_tokens: int
    max_input_tokens: int
    reserved_output_tokens: int
    safety_tokens: int
    tool_schema_tokens: int
    message_tokens: int
    history_tokens: int
    retrieved_context_tokens: int
    per_tool_result_tokens: int
    artifact_result_tokens: int

    @classmethod
    def build(
        cls,
        *,
        model: str = "",
        tools: Iterable[dict[str, Any]] | None = None,
        context_window_tokens: int = 0,
        max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
        reserved_output_tokens: int = DEFAULT_OUTPUT_TOKENS,
        safety_tokens: int = DEFAULT_SAFETY_TOKENS,
    ) -> "RuntimeContextBudget":
        window = resolve_model_context_tokens(model, context_window_tokens)
        output = max(256, int(reserved_output_tokens or DEFAULT_OUTPUT_TOKENS))
        safety = max(512, int(safety_tokens or DEFAULT_SAFETY_TOKENS))
        tool_tokens = estimate_json_tokens(list(tools or []))
        available = window - output - safety - tool_tokens
        if available < 2048:
            raise ValueError(
                "runtime context budget is impossible: "
                f"window={window}, output={output}, safety={safety}, "
                f"tool_schema={tool_tokens}, available={available}"
            )
        input_cap = max(2048, min(int(max_input_tokens or DEFAULT_MAX_INPUT_TOKENS), available))

        history = max(1500, min(8000, input_cap // 5))
        retrieved = max(800, min(3000, input_cap // 12))
        per_tool = max(1200, min(12_000, input_cap // 3))
        artifact = max(per_tool, min(30_000, (input_cap * 2) // 3))
        return cls(
            context_window_tokens=window,
            max_input_tokens=input_cap,
            reserved_output_tokens=output,
            safety_tokens=safety,
            tool_schema_tokens=tool_tokens,
            message_tokens=input_cap,
            history_tokens=history,
            retrieved_context_tokens=retrieved,
            per_tool_result_tokens=per_tool,
            artifact_result_tokens=artifact,
        )

    def as_dict(self) -> dict[str, int]:
        return asdict(self)
