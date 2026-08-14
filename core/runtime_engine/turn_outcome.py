"""Pure turn outcome projection shared by the QueryLoop exit paths."""
from __future__ import annotations

from typing import Any


def derive_execution_outcome(tool_results: list[Any]) -> str:
    """Return complete, partial, failed, or unknown from tool facts only."""
    if any(bool(getattr(result, "execution_may_continue", False)) for result in tool_results):
        return "unknown"
    successful = sum(1 for result in tool_results if bool(getattr(result, "ok", False)))
    failed = len(tool_results) - successful
    if successful and failed:
        return "partial"
    if failed:
        return "failed"
    return "complete"
