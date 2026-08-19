"""Stable identities for durable conversation message projections."""

from __future__ import annotations

import hashlib


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
