# core/tools/registry.py
"""ToolRegistry — tool registration, discovery, and validation.

Stores ToolSpec + handler pairs. Does NOT execute tools.
Handlers are stored separately from spec metadata.
list_tools() returns metadata only (no handler callables).
"""

from typing import Callable, Optional
from core.tools.schemas import ToolSpec


class ToolRegistry:
    """Registry of registered tools with their handler functions."""

    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable] = {}

    def register_tool(self, spec: ToolSpec, handler: Callable) -> None:
        """Register a tool with its handler function.

        Args:
            spec: ToolSpec with metadata and safety contract
            handler: Callable(invocation: ToolInvocation) -> dict

        Raises:
            ValueError: if tool_id is empty, already registered, or risk_level=forbidden
        """
        if not spec.tool_id:
            raise ValueError("ToolSpec.tool_id is required")
        if spec.tool_id in self._specs:
            raise ValueError(f"Tool '{spec.tool_id}' is already registered")
        if spec.risk_level == "forbidden":
            raise ValueError(f"Cannot register forbidden-risk tool: {spec.tool_id}")
        if not callable(handler):
            raise ValueError(f"Handler for '{spec.tool_id}' is not callable")

        try:
            from core.tools.tool_namespace import enrich_spec
            spec = enrich_spec(spec)
        except Exception:
            pass

        self._specs[spec.tool_id] = spec
        self._handlers[spec.tool_id] = handler

    def get_tool(self, tool_id: str) -> Optional[ToolSpec]:
        """Get ToolSpec by tool_id. Returns None if not found."""
        try:
            from core.tools.tool_namespace import resolve_tool_id
            tool_id = resolve_tool_id(tool_id)
        except Exception:
            pass
        return self._specs.get(tool_id)

    def get_handler(self, tool_id: str) -> Optional[Callable]:
        """Get handler function by tool_id. Returns None if not found."""
        try:
            from core.tools.tool_namespace import resolve_tool_id
            tool_id = resolve_tool_id(tool_id)
        except Exception:
            pass
        return self._handlers.get(tool_id)

    def list_tools(self) -> list:
        """List all registered tools as metadata dicts.

        Does NOT return handler callables — metadata only.
        """
        return [spec.as_dict() for spec in self._specs.values()]

    def list_enabled(self) -> list:
        """List only enabled tools (metadata only)."""
        return [spec.as_dict() for spec in self._specs.values() if spec.enabled]

    def is_enabled(self, tool_id: str) -> bool:
        """Check if a tool is registered and enabled."""
        try:
            from core.tools.tool_namespace import resolve_tool_id
            tool_id = resolve_tool_id(tool_id)
        except Exception:
            pass
        spec = self._specs.get(tool_id)
        return spec is not None and spec.enabled

    def count(self) -> int:
        """Number of registered tools."""
        return len(self._specs)

    def count_enabled(self) -> int:
        """Number of enabled tools."""
        return sum(1 for s in self._specs.values() if s.enabled)
