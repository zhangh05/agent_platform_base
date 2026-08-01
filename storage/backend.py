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
        with self.psycopg.connect(self.dsn, connect_timeout=3) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS agent_platform_records (record_key TEXT PRIMARY KEY, value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")

    def read(self, key: str) -> dict[str, Any] | None:
        with self.psycopg.connect(self.dsn, connect_timeout=3) as connection:
            row = connection.execute("SELECT value FROM agent_platform_records WHERE record_key=%s", (key,)).fetchone()
            return dict(row[0]) if row else None

    def write(self, key: str, value: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb
        with self.psycopg.connect(self.dsn, connect_timeout=3) as connection:
            connection.execute("INSERT INTO agent_platform_records(record_key,value) VALUES(%s,%s) ON CONFLICT(record_key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()", (key, Jsonb(value)))

    def delete(self, key: str) -> bool:
        with self.psycopg.connect(self.dsn, connect_timeout=3) as connection:
            cursor = connection.execute("DELETE FROM agent_platform_records WHERE record_key=%s", (key,))
            return cursor.rowcount > 0

    def health(self) -> dict[str, Any]:
        with self.psycopg.connect(self.dsn, connect_timeout=3) as connection:
            value = connection.execute("SELECT 1").fetchone()
            return {"connected": bool(value and value[0] == 1)}


def backend_mode() -> str:
    import os
    explicit = os.environ.get("AGENT_PLATFORM_RECORD_STORE_MODE", "").strip().lower()
    legacy = os.environ.get("AGENT_PLATFORM_STORAGE_MODE", "filesystem").strip().lower() or "filesystem"
    return explicit or ("filesystem" if legacy in {"s3", "object"} else legacy)


def validate_backend_configuration() -> list[str]:
    import os
    mode = backend_mode()
    object_mode = os.environ.get("AGENT_PLATFORM_OBJECT_STORE_MODE", "").strip().lower()
    if not object_mode:
        legacy = os.environ.get("AGENT_PLATFORM_STORAGE_MODE", "filesystem").strip().lower()
        object_mode = "s3" if legacy in {"s3", "object"} else "local"
    errors: list[str] = []
    if mode in {"postgres", "postgresql"} and not os.environ.get("AGENT_PLATFORM_DATABASE_URL"):
        errors.append("AGENT_PLATFORM_DATABASE_URL is required for postgres storage")
    if object_mode in {"s3", "object"} and not os.environ.get("AGENT_PLATFORM_OBJECT_STORE_BUCKET"):
        errors.append("AGENT_PLATFORM_OBJECT_STORE_BUCKET is required for object storage")
    if mode not in {"filesystem", "local", "postgres", "postgresql"}:
        errors.append(f"unsupported storage mode: {mode}")
    if object_mode not in {"filesystem", "local", "s3", "object"}:
        errors.append(f"unsupported object storage mode: {object_mode}")
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
