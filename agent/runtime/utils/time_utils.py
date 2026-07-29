# agent/runtime/utils/time_utils.py
"""Single source of truth for timestamp formatting.

All persisted/user-visible timestamps are timezone-aware ISO-8601 UTC strings.
Runtime arithmetic should convert through ``from_iso`` / ``duration_ms``.
"""

from __future__ import annotations

from storage.time_utils import from_iso, now_iso, to_iso


def duration_ms(started_at: str, finished_at: str) -> int:
    """Return the millisecond duration between two ISO-8601 timestamps.

    Rounded to the nearest integer (the canonical ToolResult /
    RuntimeStep.duration_ms shape is ``int``, not ``float``).
    """
    return int(round((from_iso(finished_at) - from_iso(started_at)) * 1000))
