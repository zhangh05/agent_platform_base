"""Workspace audit-log repository."""

from __future__ import annotations

import json
from typing import Any

from storage.records import workspace_record_dir


def list_audit_entries(workspace_id: str, *, log_level: str = "info", limit: int = 20) -> list[dict[str, Any]]:
    level_filter = str(log_level or "info").lower()
    capped = max(1, min(int(limit or 20), 100))
    log_dir = workspace_record_dir(workspace_id, "audit")
    files = sorted(log_dir.glob("*.json"))[-capped:] if log_dir.exists() else []
    entries: list[dict[str, Any]] = []
    for path in files:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8")[:20000])
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        level = str(parsed.get("level", parsed.get("severity", "info"))).lower()
        if level_filter == "error" and level != "error":
            continue
        if level_filter == "warn" and level not in {"warn", "warning", "error"}:
            continue
        entries.append(parsed)
    return entries
