# agent/protocol/tool_call.py
"""ToolCall — LLM-requested tool invocation."""

from dataclasses import dataclass, field

# Whitelist of valid `source` values. Anything else is treated as
# "unknown" downstream so it can't be used to bypass argument_source
# tracking or other policy gates.
_VALID_SOURCES = frozenset({"llm", "system", "deterministic", "memory", "rag"})


@dataclass
class ToolCall:
    call_id: str = ""
    llm_tool_name: str = ""       # LLM-safe name (with __)
    real_tool_id: str = ""        # Real tool_id (with .)
    arguments: dict = field(default_factory=dict)
    source: str = "llm"           # llm | system | deterministic | memory | rag

    def __setattr__(self, name, value):
        # Enforce source whitelist so untrusted LLM output can't mark
        # itself as "system" or "memory" to skip policy checks.
        if name == "source" and value not in _VALID_SOURCES:
            value = "unknown"
        super().__setattr__(name, value)
