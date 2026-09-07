"""Context accounting helpers for the SSOT runtime.

The values in this module are telemetry for provider capacity planning. They
must never remove or shorten model-visible messages, tool results, or evidence.
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
    """Return complete text; ``max_tokens`` is accounting-only."""
    return str(text or ""), False


def project_json_to_tokens(value: Any, max_tokens: int) -> tuple[Any, bool]:
    """Return a structure-preserving JSON projection within ``max_tokens``.

    This is intended for API and audit projections, not model context. Unlike
    slicing serialized JSON, it keeps dictionaries and lists parseable and
    leaves an explicit marker wherever values or collection members were
    omitted.
    """
    return copy.deepcopy(value), False


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
