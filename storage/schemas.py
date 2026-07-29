# storage/schemas.py
"""Unified storage data models."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class FileRecord:
    """A managed file entry in the workspace file index."""
    file_id: str
    workspace_id: str
    logical_type: str          # user_upload | chat_attachment | knowledge_source | report | ...
    file_kind: str             # text | binary | pdf | markdown | json | ...
    path: str                  # workspace-relative path
    original_name: str = ""
    mime_type: str = ""
    binary: bool = False
    size_bytes: int = 0
    sha256: str = ""
    created_at: str = ""
    created_by: str = "system"
    session_id: str = ""
    run_id: str = ""
    source: str = ""           # artifact_upload | knowledge_import | agent | ...
    sensitivity: str = "internal"
    lifecycle: str = "active"  # active | soft_deleted | archived
    retention_policy: str = "workspace_default"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FileReference:
    """A cross-reference linking a file to an owner entity."""
    ref_id: str
    workspace_id: str
    file_id: str
    owner_type: str   # session | run | message | artifact | knowledge_source | ...
    owner_id: str
    relation: str     # source | output | attachment | normalized | ...
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)
