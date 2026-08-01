"""Pluggable data-plane contracts; filesystem remains the default adapter."""

from __future__ import annotations

from typing import Any, Protocol


class RecordBackend(Protocol):
    def read(self, key: str) -> dict[str, Any] | None: ...
    def write(self, key: str, value: dict[str, Any]) -> None: ...
    def delete(self, key: str) -> bool: ...


class ObjectBackend(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> bool: ...


def backend_mode() -> str:
    import os
    return os.environ.get("AGENT_PLATFORM_STORAGE_MODE", "filesystem").strip().lower() or "filesystem"


def validate_backend_configuration() -> list[str]:
    import os
    mode = backend_mode()
    errors: list[str] = []
    if mode in {"postgres", "postgresql"} and not os.environ.get("AGENT_PLATFORM_DATABASE_URL"):
        errors.append("AGENT_PLATFORM_DATABASE_URL is required for postgres storage")
    if mode in {"s3", "object"} and not os.environ.get("AGENT_PLATFORM_OBJECT_STORE_BUCKET"):
        errors.append("AGENT_PLATFORM_OBJECT_STORE_BUCKET is required for object storage")
    if mode not in {"filesystem", "local", "postgres", "postgresql", "s3", "object"}:
        errors.append(f"unsupported storage mode: {mode}")
    return errors
