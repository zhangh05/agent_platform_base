"""Request-scoped storage identity.

Business records are isolated by the pair ``(username, workspace_id)``. Every
authenticated user, including the configured administrator, is stored under a
stable per-user directory inside each workspace.
"""

from __future__ import annotations

import contextvars
import hashlib
import re
from contextlib import contextmanager
from functools import wraps
from typing import Iterator

_principal: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agent_platform_storage_principal", default=""
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
    value = str(username or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-") or "user"
    digest = hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:12]
    return f"{safe[:40]}-{digest}"


def bind_storage_principal(func):
    """Capture the current principal for a new thread or executor task."""
    username = current_storage_principal()

    @wraps(func)
    def bound(*args, **kwargs):
        with storage_principal(username):
            return func(*args, **kwargs)

    return bound
