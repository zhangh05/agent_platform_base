"""Pluggable data-plane contracts; filesystem remains the default adapter."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol


class RecordBackend(Protocol):
    def read(self, key: str) -> dict[str, Any] | None: ...
    def write(self, key: str, value: dict[str, Any]) -> None: ...
    def delete(self, key: str) -> bool: ...


class ObjectBackend(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> bool: ...


class PostgresRecordBackend:
    """Small JSON document adapter used by control-plane runtime records."""

    def __init__(self, dsn: str):
        import psycopg
        self.psycopg = psycopg
        self.dsn = dsn
        with self.psycopg.connect(self.dsn) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS agent_platform_records (record_key TEXT PRIMARY KEY, value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")

    def read(self, key: str) -> dict[str, Any] | None:
        with self.psycopg.connect(self.dsn) as connection:
            row = connection.execute("SELECT value FROM agent_platform_records WHERE record_key=%s", (key,)).fetchone()
            return dict(row[0]) if row else None

    def write(self, key: str, value: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb
        with self.psycopg.connect(self.dsn) as connection:
            connection.execute("INSERT INTO agent_platform_records(record_key,value) VALUES(%s,%s) ON CONFLICT(record_key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()", (key, Jsonb(value)))

    def delete(self, key: str) -> bool:
        with self.psycopg.connect(self.dsn) as connection:
            cursor = connection.execute("DELETE FROM agent_platform_records WHERE record_key=%s", (key,))
            return cursor.rowcount > 0


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


def get_record_backend():
    import os
    mode = backend_mode()
    if mode in {"postgres", "postgresql"}:
        dsn = os.environ.get("AGENT_PLATFORM_DATABASE_URL", "").strip()
        if not dsn:
            raise RuntimeError("AGENT_PLATFORM_DATABASE_URL is required")
        return _postgres_backend(dsn)
    return None


@lru_cache(maxsize=4)
def _postgres_backend(dsn: str) -> PostgresRecordBackend:
    return PostgresRecordBackend(dsn)
