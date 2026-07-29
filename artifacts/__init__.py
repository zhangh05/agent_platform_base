# artifacts/__init__.py
"""Artifact / File Pipeline — unified file input/output management."""

from artifacts.schemas import ArtifactRecord, ArtifactIndex, RunArtifactIndex
from artifacts.store import (
    save_artifact, get_artifact, read_artifact_content,
    list_artifacts, delete_artifact, promote_artifact,
    summarize_artifact_content, get_run_artifacts,
    get_artifact_governance, artifact_governance_summary,
)
from artifacts.redaction import redact_artifact_content, contains_secret
from artifacts.classifier import classify_file
