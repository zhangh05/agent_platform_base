"""Object storage abstraction with a safe local default."""

from __future__ import annotations

import hashlib
from pathlib import Path

from storage.atomic_io import atomic_write_bytes
from storage.paths import get_workspace_root, runtime_root, workspace_root
from storage.backend import backend_mode


class LocalObjectStore:
    def __init__(self, root: str | Path | None = None, *, workspace_id: str = ""):
        # Business objects name a workspace and inherit the authenticated
        # user's data root. The global default is platform-control only.
        self.root = Path(root) if root else (
            workspace_root(workspace_id) / "objects" if workspace_id else runtime_root() / "objects"
        )

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

    def health(self) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".readiness"
        atomic_write_bytes(probe, b"ok")
        probe.unlink(missing_ok=True)
        return {"root_writable": True}


class S3ObjectStore:
    def __init__(self, bucket: str, prefix: str = ""):
        import os
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        endpoint_url = os.environ.get("AGENT_PLATFORM_S3_ENDPOINT_URL", "").strip() or None
        addressing_style = os.environ.get("AGENT_PLATFORM_S3_ADDRESSING_STYLE", "auto").strip() or "auto"
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            config=Config(
                connect_timeout=3,
                read_timeout=3,
                retries={"max_attempts": 1},
                s3={"addressing_style": addressing_style},
            ),
        )

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

    def health(self) -> dict:
        self.client.head_bucket(Bucket=self.bucket)
        return {"connected": True}


def object_store_mode() -> str:
    import os
    explicit = os.environ.get("AGENT_PLATFORM_OBJECT_STORE_MODE", "").strip().lower()
    if explicit:
        return "s3" if explicit in {"s3", "object"} else "local"
    legacy = os.environ.get("AGENT_PLATFORM_STORAGE_MODE", "filesystem").strip().lower()
    return "s3" if legacy in {"s3", "object"} else "local"


def _workspace_object_prefix(workspace_id: str) -> str:
    """Build the same user/workspace namespace for remote object stores."""
    root = workspace_root(workspace_id)
    try:
        relative = root.relative_to(get_workspace_root())
    except ValueError as exc:
        raise ValueError("workspace object root escapes storage root") from exc
    return (relative / "objects").as_posix()


def get_object_store(workspace_id: str = ""):
    import os
    if object_store_mode() == "s3":
        bucket = os.environ.get("AGENT_PLATFORM_OBJECT_STORE_BUCKET", "").strip()
        if not bucket:
            raise RuntimeError("AGENT_PLATFORM_OBJECT_STORE_BUCKET is required")
        base_prefix = os.environ.get("AGENT_PLATFORM_OBJECT_STORE_PREFIX", "").strip("/")
        scoped_prefix = "/".join(part for part in (base_prefix, _workspace_object_prefix(workspace_id)) if part) if workspace_id else base_prefix
        return S3ObjectStore(bucket, scoped_prefix)
    return LocalObjectStore(workspace_id=workspace_id)
