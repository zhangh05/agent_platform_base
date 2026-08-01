"""Object storage abstraction with a safe local default."""

from __future__ import annotations

import hashlib
from pathlib import Path

from storage.atomic_io import atomic_write_bytes
from storage.paths import runtime_root
from storage.backend import backend_mode


class LocalObjectStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else runtime_root() / "objects"

    def _path(self, key: str) -> Path:
        parts = str(key).split("/")
        if any(part == ".." for part in parts):
            raise ValueError("object key escapes storage root")
        clean = "/".join(part for part in parts if part not in ("", "."))
        if not clean:
            raise ValueError("object key is required")
        path = (self.root / clean).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("object key escapes storage root")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        atomic_write_bytes(self._path(key), bytes(data))
        return f"local://{key}"

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def content_hash(self, key: str) -> str | None:
        data = self.get(key)
        return hashlib.sha256(data).hexdigest() if data is not None else None


class S3ObjectStore:
    def __init__(self, bucket: str, prefix: str = ""):
        import boto3
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3")

    def _key(self, key: str) -> str:
        LocalObjectStore(Path("/tmp"))._path(key)
        return "/".join(part for part in (self.prefix, key.strip("/")) if part)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        object_key = self._key(key)
        self.client.put_object(Bucket=self.bucket, Key=object_key, Body=bytes(data), ContentType=content_type)
        return f"s3://{self.bucket}/{object_key}"

    def get(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None
        except Exception as exc:
            response = getattr(exc, "response", {})
            code = str((response.get("Error") or {}).get("Code") or "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

    def delete(self, key: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))
        return True


def get_object_store():
    import os
    if backend_mode() in {"s3", "object"}:
        bucket = os.environ.get("AGENT_PLATFORM_OBJECT_STORE_BUCKET", "").strip()
        if not bucket:
            raise RuntimeError("AGENT_PLATFORM_OBJECT_STORE_BUCKET is required")
        return S3ObjectStore(bucket, os.environ.get("AGENT_PLATFORM_OBJECT_STORE_PREFIX", ""))
    return LocalObjectStore()
