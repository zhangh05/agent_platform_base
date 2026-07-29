"""Storage-safe timestamp helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Union


def to_iso(ts: Union[float, str, None]) -> str:
    if ts is None:
        return now_iso()
    if isinstance(ts, str):
        from_iso(ts)
        return ts
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def from_iso(value: str) -> float:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware ISO-8601")
    return dt.timestamp()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
