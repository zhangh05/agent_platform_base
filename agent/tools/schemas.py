# agent/tools/schemas.py
"""ToolSpec and LLMToolSpec — tool metadata."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolSpec:
    tool_id: str = ""
    name: str = ""
    category: str = ""
    description: str = ""
    risk_level: str = "low"
    enabled: bool = True
    requires_approval: bool = False
    input_schema: dict = field(default_factory=dict)
    callable_by_llm: bool = True
    forbidden: bool = False
    source: str = "runtime"
    timeout_seconds: int = 30
    permission_action: str = ""  # read | write | exec | network
    metadata: dict = field(default_factory=dict)


@dataclass
class LLMToolSpec:
    name: str = ""           # LLM-safe name (__ format)
    description: str = ""
    parameters: dict = field(default_factory=dict)
    real_tool_id: str = ""   # real tool_id (. format)

    def to_openai_function(self) -> dict:
        """Convert to OpenAI function calling format."""
        parameters = dict(self.parameters or {})
        parameters.setdefault("type", "object")
        parameters.setdefault("properties", {})
        parameters.setdefault("required", [])
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }

    def to_openai_function_compact(self) -> dict:
        """Compact version for defer_loading — name + one-liner only.

        Minimizes token usage for non-core tools. The runtime decides which
        full schemas are visible; there is no LLM catalog-search expansion path.
        """
        parameters = dict(self.parameters or {})
        # Only include required params in compact mode — save tokens
        required = parameters.get("required", [])
        compact_params = {
            "type": "object",
            "properties": {k: parameters["properties"][k]
                          for k in required
                          if k in parameters.get("properties", {})},
        }
        if required:
            compact_params["required"] = required
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": compact_params,
            },
        }
