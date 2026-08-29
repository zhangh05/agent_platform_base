"""Stable identities for durable conversation message projections."""

from __future__ import annotations

import hashlib
from typing import Any


def user_message_storage_run_id(client_request_id: str, run_id: str) -> str:
    """Return the single durable key for one accepted user request.

    A client request id survives retry, stream reconnect and the pre-execution
    checkpoint.  When it is present, every writer of that user message must use
    its deterministic projection key instead of a per-attempt turn id.
    """
    stable_request = str(client_request_id or "").strip()
    if stable_request:
        digest = hashlib.sha256(stable_request.encode("utf-8")).hexdigest()
        return f"request_{digest}"
    return str(run_id or "").strip()


def workbench_message_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Project server-validated workbench context into safe chat UI metadata."""
    source = metadata if isinstance(metadata, dict) else {}
    context = source.get("workbench_context")
    if not isinstance(context, dict):
        return {}
    skill_id = str(context.get("skill_id") or "").strip()
    skill_name = str(context.get("skill_name") or "").strip()
    if not skill_id or not skill_name:
        return {}
    return {
        "workbench_skill": {
            "skill_id": skill_id,
            "name": skill_name[:100],
        }
    }
