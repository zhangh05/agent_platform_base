"""Request-scoped storage identity.

Business records are isolated by the pair ``(immutable_user_id, workspace_id)``.
Every authenticated user, including the configured administrator, is stored
under a stable per-user directory inside each workspace.
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from functools import wraps
from typing import Iterator

_principal: contextvars.ContextVar[str] = contextvars.ContextVar(
    "lzcore_storage_principal", default=""
)


def current_storage_principal() -> str:
    return _principal.get().strip()


def set_storage_principal(username: str):
    return _principal.set(str(username or "").strip())


def reset_storage_principal(token) -> None:
    try:
        _principal.reset(token)
    except RuntimeError:
        # Flask streaming responses may tear down both the original request
        # context and its copied streaming context. A ContextVar token is
        # single-use, so the second cleanup is intentionally a no-op.
        pass


@contextmanager
def storage_principal(username: str) -> Iterator[None]:
    token = set_storage_principal(username)
    try:
        yield
    finally:
        reset_storage_principal(token)


def principal_storage_key(username: str) -> str:
    """Return the immutable storage ID for an authenticated principal.

    A username is a login/display attribute, not a durable storage key.  The
    identity adapter persists an ID for managed users; the environment-defined
    bootstrap administrator has a deterministic system ID until it is managed
    by that adapter.
    """
    from backend.core.identity import resolve_user_storage_id

    return resolve_user_storage_id(username)


def known_storage_principals() -> list[str]:
    """Return configured and identity-managed users for restart recovery jobs."""
    usernames = {os.environ.get("LZCORE_LOGIN_USERNAME", "").strip()}
    if (
        os.environ.get("LZCORE_API_TOKEN", "").strip()
        or os.environ.get("LZCORE_API_TOKEN_FILE", "").strip()
    ):
        usernames.add("api-token")
    try:
        from backend.core.identity import list_users
        usernames.update(str(item.get("username") or "").strip() for item in list_users())
    except Exception:
        pass
    return sorted(username for username in usernames if username)


def bind_storage_principal(func):
    """Capture the current principal for a new thread or executor task."""
    username = current_storage_principal()

    @wraps(func)
    def bound(*args, **kwargs):
        with storage_principal(username):
            return func(*args, **kwargs)

    return bound
