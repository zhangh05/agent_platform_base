# agent/runtime/__init__.py
"""Runtime package exports.

Keep this module light. Eager imports here would pull in unnecessary dependencies,
creating circular imports during store/tool startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.runtime.result import AgentResult

__all__ = [
    "AgentResult",
]


def __getattr__(name: str):
    if name == "AgentResult":
        from agent.runtime.result import AgentResult
        return AgentResult
    raise AttributeError(name)
