# artifacts/schemas.py
"""ArtifactRecord, ArtifactIndex, RunArtifactIndex schemas."""

import uuid
from dataclasses import dataclass, field
from typing import Optional

from storage.time_utils import now_iso

ARTIFACT_TYPES = {
    "input_data", "output_data", "report",
    "knowledge_doc", "knowledge_index",
    "template", "sample", "trace_export",
    "temp", "unknown",
}
SCOPES = {"run", "workspace", "shared", "global", "temp"}
SENSITIVITIES = {"public", "internal", "sensitive", "secret"}
LIFECYCLES = {"quarantined", "active", "promoted", "archived", "deleted"}


def _utcnow() -> str:
    return now_iso()


@dataclass
class ArtifactRecord:
    artifact_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    workspace_id: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    module: Optional[str] = None
    skill: Optional[str] = None
    capability_id: Optional[str] = None
    artifact_type: str = "unknown"
    title: str = ""
    summary: str = ""
    description: Optional[str] = None
    scope: str = "workspace"
    sensitivity: str = "internal"
    lifecycle: str = "active"
    path: str = ""
    relative_path: str = ""
    mime_type: str = ""
    file_ext: str = ""
    size_bytes: int = 0
    sha256: str = ""
    file_id: str = ""  # Managed file ID from storage.file_store
    source: str = "module_output"
    created_by: str = ""
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    expires_at: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)
    redaction_applied: bool = False
    parent_artifact_id: Optional[str] = None
    derived_from: list = field(default_factory=list)
    references: list = field(default_factory=list)

    def as_dict(self, include_content: bool = False) -> dict:
        d = {
            "artifact_id": self.artifact_id, "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "run_id": self.run_id, "module": self.module, "skill": self.skill,
            "capability_id": self.capability_id, "artifact_type": self.artifact_type,
            "title": self.title, "summary": self.summary,
            "scope": self.scope, "sensitivity": self.sensitivity,
            "lifecycle": self.lifecycle, "relative_path": self.relative_path,
            "mime_type": self.mime_type, "file_ext": self.file_ext,
            "size_bytes": self.size_bytes, "sha256_short": self.sha256[:12] if self.sha256 else "",
            "source": self.source, "created_at": self.created_at,
            "updated_at": self.updated_at, "expires_at": self.expires_at,
            "tags": self.tags, "redaction_applied": self.redaction_applied,
            "parent_artifact_id": self.parent_artifact_id,
            "created_by": self.created_by,
        }
        # Always include metadata in the meta file so that
        # callers can recover the original metadata (e.g. review service
        # needs manual_review_items). The include_content flag still
        # controls the larger content body. Existing consumers that use
        # sanitize_record() for the API surface are unaffected.
        d["metadata"] = self.metadata
        if include_content:
            pass  # placeholder for future larger content body
        return d

    def as_summary(self) -> dict:
        return {
            "artifact_id": self.artifact_id, "artifact_type": self.artifact_type,
            "title": self.title, "summary": self.summary,
            "scope": self.scope, "sensitivity": self.sensitivity,
            "size_bytes": self.size_bytes,
        }


@dataclass
class ArtifactIndex:
    workspace_id: str = ""
    updated_at: str = ""
    artifact_ids: list = field(default_factory=list)
    artifact_count: int = 0

    def as_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "updated_at": self.updated_at,
            "artifact_ids": self.artifact_ids,
            "artifact_count": self.artifact_count,
        }


@dataclass
class RunArtifactIndex:
    workspace_id: str = ""
    run_id: str = ""
    input_artifacts: list = field(default_factory=list)
    output_artifacts: list = field(default_factory=list)
    report_artifacts: list = field(default_factory=list)
    temp_artifacts: list = field(default_factory=list)
    updated_at: str = ""

    def as_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id, "run_id": self.run_id,
            "input_artifacts": self.input_artifacts,
            "output_artifacts": self.output_artifacts,
            "report_artifacts": self.report_artifacts,
            "temp_artifacts": self.temp_artifacts,
            "updated_at": self.updated_at,
        }
