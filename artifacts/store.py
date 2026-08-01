# artifacts/store.py
"""Artifact store — save/read/list/delete/promote with policy, redaction, index.

artifact_id = art_<uuid[:16]> (unique per object, not content-based).
sha256 = content fingerprint (for dedup reference, not for identity).
source_path validated against allowed directories only.

Current runtime storage model:
- artifact metadata is written atomically by this store
- artifact content is stored through storage.file_store
  the projection event
- artifact metadata is stored in workspaces/<ws>/index/artifacts.jsonl as a
  read model carrying ssot_event_id
"""

import json, hashlib, os, re, time, shutil, uuid
from pathlib import Path
from typing import Optional

from artifacts.schemas import ArtifactRecord, ArtifactIndex, RunArtifactIndex
from artifacts.redaction import redact_artifact_content, contains_secret, redact_metadata
from artifacts.classifier import classify_file
from storage.schemas import FileRecord
import logging
from storage.time_utils import now_iso
from storage.paths import runtime_root
from storage.artifact_metadata_store import list_artifact_records, read_artifact_index

_LOG = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

# Allowed source directories for source_path reads. Removed storage directories
# are intentionally not allowed for runtime reads.
ALLOWED_SOURCE_DIRS = [
    "files/data",
    "files/tmp",
    "shared",
]

MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB


def _get_max_size() -> int:
    """Resolve max artifact size from env or default."""
    try:
        mb = int(os.environ.get("AGENT_PLATFORM_MAX_UPLOAD_MB", "0"))
        if mb > 0:
            return mb * 1024 * 1024
    except ValueError:
        pass
    return MAX_FILE_SIZE


def _workspace_path(workspace_id: str):
    from storage.paths import workspace_root
    return workspace_root(workspace_id)


def _safe_name(name: str) -> str:
    # Allow ASCII, Chinese (CJK), digits, and safe punctuation in filenames.
    return re.sub(r'[^a-zA-Z0-9_.\-\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\U0002a700-\U0002ebef]', '_', name or "artifact")[:120]


def _new_artifact_id() -> str:
    return f"art_{uuid.uuid4().hex[:16]}"


def _read_artifact_record_dicts(ws_id: str) -> list[dict]:
    return list_artifact_records(ws_id)


def _artifact_record_from_dict(data: dict) -> ArtifactRecord:
    return ArtifactRecord(**{
        k: v for k, v in data.items() if k in ArtifactRecord.__dataclass_fields__
    })


def _records_in_index_order(ws_id: str) -> list[ArtifactRecord]:
    """Load artifact metadata once and project the current record per id."""
    latest: dict[str, dict] = {}
    for data in _read_artifact_record_dicts(ws_id):
        artifact_id = str(data.get("artifact_id", "") or "")
        if artifact_id:
            latest[artifact_id] = data
    ordered_ids = list(_load_index(ws_id).artifact_ids)
    ordered_ids.extend(artifact_id for artifact_id in latest if artifact_id not in ordered_ids)
    records: list[ArtifactRecord] = []
    for artifact_id in ordered_ids:
        data = latest.get(artifact_id)
        if data is None:
            continue
        try:
            records.append(_artifact_record_from_dict(data))
        except (TypeError, ValueError):
            _LOG.warning("artifacts.store: invalid artifact record %s", artifact_id, exc_info=True)
    return records


def _load_index(ws_id: str) -> ArtifactIndex:
    try:
        d = read_artifact_index(ws_id)
        if d.get("artifact_ids"):
            return ArtifactIndex(workspace_id=ws_id, artifact_ids=d.get("artifact_ids", []),
                                 artifact_count=d.get("artifact_count", 0),
                                 updated_at=d.get("updated_at", ""))
    except (OSError, ValueError):
        _LOG.warning("artifacts.store: invalid artifact index", exc_info=True)

    # Fallback to the metadata JSONL if the lightweight sys index is absent.
    records = _read_artifact_record_dicts(ws_id)
    if records:
        ids = [r.get("artifact_id", "") for r in records if r.get("artifact_id")]
        return ArtifactIndex(workspace_id=ws_id, artifact_ids=ids,
                             artifact_count=len(ids), updated_at="")
    return ArtifactIndex(workspace_id=ws_id)


def _record_meta_dict(rec: ArtifactRecord) -> dict:
    data = rec.as_dict()
    # Keep complete storage-critical metadata in the JSONL record even if the
    # public schema summary omits some fields.
    data.update({
        "path": rec.path,
        "relative_path": rec.relative_path,
        "description": rec.description,
        "mime_type": rec.mime_type,
        "file_ext": rec.file_ext,
        "size_bytes": rec.size_bytes,
        "sha256": rec.sha256,
        "file_id": rec.file_id,
        "created_by": rec.created_by,
        "metadata": rec.metadata,
        "derived_from": rec.derived_from,
        "references": rec.references,
    })
    return data


def _save_artifact_record(rec: ArtifactRecord, *, add_to_index: bool = False) -> None:
    """Upsert one ArtifactRecord into index/artifacts.jsonl."""
    from storage.artifact_metadata_store import upsert_artifact_record

    upsert_artifact_record(rec.workspace_id, _record_meta_dict(rec), add_to_index=add_to_index)


def _logical_type_for_artifact(artifact_type: str) -> str:
    """Map artifact_type to a storage logical_type for FileRecord tracking."""
    mapping = {
        "input_data": "data_input",
        "output_data": "artifact_output",
        "report": "report",
        "message_large_content": "message_large_content",
    }
    return mapping.get((artifact_type or "").strip(), "artifact_output")


def _validate_source_path(source_path: str, workspace_id: str = "") -> bool:
    """Strict path boundary check using resolve().relative_to()."""
    if not source_path:
        return True

    try:
        resolved = Path(source_path).resolve()
    except Exception:
        return False

    allowed_parents = []
    root_path = ROOT.resolve()

    allowed_parents.extend([
        runtime_root() / "uploads",
        root_path / "shared" / "knowledge",
        root_path / "shared" / "templates",
        root_path / "shared" / "vendor_docs",
        root_path / "shared" / "samples",
    ])
    if workspace_id:
        ws = _workspace_path(workspace_id).resolve()
        for rel in ALLOWED_SOURCE_DIRS:
            if rel.startswith("shared"):
                continue
            allowed_parents.append(ws / rel)

    for parent in allowed_parents:
        try:
            resolved.relative_to(parent.resolve())
            return True
        except ValueError:
            continue
    return False


def _normalized_title(title: str) -> str:
    return re.sub(r"\s+", " ", str(title or "").strip()).lower()


def _is_generic_report_title(title: str) -> bool:
    return _normalized_title(title) in {"", "report", "报告"}


def _report_day(rec: ArtifactRecord) -> str:
    created_at = str(getattr(rec, "created_at", "") or "")
    return created_at[:10] if len(created_at) >= 10 else ""


def _derive_report_title(content: str, fallback: str) -> str:
    """Use the first markdown H1 as the report title when callers pass 'report'."""
    if not _is_generic_report_title(fallback):
        return fallback
    for line in (content or "").splitlines()[:20]:
        line = line.strip()
        if line.startswith("# "):
            candidate = line[2:].strip()
            if candidate:
                return candidate[:120]
    return fallback


def _report_display_key(rec: ArtifactRecord, named_days: set[str]) -> tuple | None:
    """Return a stable UI key for report list dedupe.

    The store keeps every artifact immutable; this key only controls list
    presentation so intermediate LLM-generated drafts do not flood the
    Artifact Center.
    """
    if rec.artifact_type != "report":
        return ("artifact", rec.artifact_id)

    metadata = rec.metadata if isinstance(rec.metadata, dict) else {}
    report_format = str(metadata.get("report_format") or metadata.get("format") or rec.file_ext or "")
    day = _report_day(rec)
    title = _normalized_title(rec.title)
    if _is_generic_report_title(rec.title):
        if day in named_days:
            return None
        return ("report", "generic_day", day)
    return ("report", "title_day", title, day)


def _dedupe_artifacts_for_listing(records: list[ArtifactRecord]) -> list[ArtifactRecord]:
    named_days = {
        _report_day(rec)
        for rec in records
        if rec.artifact_type == "report" and not _is_generic_report_title(rec.title)
    }
    latest_by_key: dict[tuple, ArtifactRecord] = {}
    key_by_artifact_id: dict[str, tuple] = {}

    for rec in records:
        key = _report_display_key(rec, named_days)
        if key is None:
            continue
        latest_by_key[key] = rec
        key_by_artifact_id[rec.artifact_id] = key

    deduped_reversed: list[ArtifactRecord] = []
    emitted: set[tuple] = set()
    for rec in reversed(records):
        key = key_by_artifact_id.get(rec.artifact_id)
        if key is None or key in emitted:
            continue
        if latest_by_key.get(key) is rec:
            deduped_reversed.append(rec)
            emitted.add(key)
    return list(reversed(deduped_reversed))


# ═══════════════ PUBLIC API ═══════════════

def save_artifact(workspace_id: str, content: str = "", source_path: str = "",
                  artifact_type: str = "", title: str = "", scope: str = "workspace",
                  sensitivity: str = "", run_id: str = "", session_id: str = "", module: str = "",
                  skill: str = "", capability_id: str = "", metadata: dict = None,
                  tags: list = None, source: str = "module_output",
                  file_id: str = "", created_by: str = "") -> Optional[ArtifactRecord]:
    """Save an artifact. Returns ArtifactRecord or None if blocked by policy."""
    from storage.workspace_store import ensure_workspace
    ensure_workspace(workspace_id)

    if source_path and not content:
        if not _validate_source_path(source_path, workspace_id):
            return None
        sp = Path(source_path)
        if not sp.is_file():
            return None
        max_size = _get_max_size()
        file_size = sp.stat().st_size
        if file_size > max_size:
            return None
        content = sp.read_text(encoding="utf-8")
        artifact_type = artifact_type or "unknown"

    if not content:
        return None

    max_size = _get_max_size()
    if len(content.encode("utf-8")) > max_size:
        return None

    content_had_secret = contains_secret(content)
    if content_had_secret:
        if sensitivity == "secret":
            content = redact_artifact_content(content)
        else:
            # Surface the rejection while keeping secret content out of
            # non-secret artifact slots.
            _LOG.warning(
                "artifacts.store: content contains a secret but "
                "sensitivity=%r — refusing to save without explicit "
                "'secret' marker. Caller should re-issue with "
                "sensitivity='secret' so the redactor runs.",
                sensitivity,
            )
            return None

    cls = classify_file(source_path, content)
    artifact_type = artifact_type or cls["artifact_type"]
    sensitivity = sensitivity or cls["sensitivity"]
    if artifact_type == "report":
        title = _derive_report_title(content, title)

    art_id = _new_artifact_id()
    ext = cls["file_ext"] or "txt"
    logical_type = _logical_type_for_artifact(artifact_type)
    file_kind = cls["file_ext"] or ext or "text"

    try:
        from storage.file_store import get_file_record, write_agent_output
        existing = get_file_record(workspace_id, file_id) if file_id and not content_had_secret else None
        if existing and existing.get("lifecycle", "active") == "active":
            file_rec = FileRecord(**{
                key: value for key, value in existing.items()
                if key in FileRecord.__dataclass_fields__
            })
            from storage import index as file_index
            file_index.update_file_record(workspace_id, file_id, {
                "metadata": {
                    **existing.get("metadata", {}),
                    "artifact_id": art_id,
                    "artifact_type": artifact_type,
                    "storage_managed": True,
                },
            })
        else:
            file_rec = write_agent_output(
                workspace_id=workspace_id,
                content=content,
                logical_type=logical_type,
                file_kind=file_kind,
                title=title or artifact_type or art_id,
                ext=ext,
                source=source,
                run_id=run_id,
                sensitivity=sensitivity,
                metadata={
                    "artifact_id": art_id,
                    "artifact_type": artifact_type,
                    "storage_managed": True,
                },
            )
    except Exception:
        # If FileStore fails, artifact creation fails.
        return None

    ws = _workspace_path(workspace_id)
    fpath = (ws / file_rec.path).resolve()
    fname = file_rec.path
    title = title or f"{artifact_type}: {art_id}"
    rec = ArtifactRecord(
        artifact_id=art_id, workspace_id=workspace_id, session_id=session_id, run_id=run_id,
        module=module, skill=skill, capability_id=capability_id,
        artifact_type=artifact_type, title=title,
        scope=scope, sensitivity=sensitivity, lifecycle="active",
        path=str(fpath), relative_path=fname,
        mime_type=cls["mime_type"], file_ext=cls["file_ext"],
        size_bytes=file_rec.size_bytes, sha256=file_rec.sha256, source=source,
        file_id=file_rec.file_id,
        created_by=str(created_by or "")[:160],
        metadata=redact_metadata(metadata or {}),
        tags=tags or cls["tags"],
        redaction_applied=cls["contains_secret"],
    )

    _save_artifact_record(rec, add_to_index=True)

    if run_id:
        _update_run_index(workspace_id, run_id, art_id, artifact_type, title)

    try:
        from storage.reference_index import add_reference
        if rec.file_id:
            add_reference(workspace_id, rec.file_id, "artifact", art_id, "output",
                          metadata={"artifact_type": artifact_type, "run_id": run_id})
        src_file = (metadata or {}).get("source_file_id")
        if src_file:
            add_reference(workspace_id, src_file, "artifact", art_id, "source",
                          metadata={"artifact_type": artifact_type, "run_id": run_id})
    except Exception:
        _LOG.warning("artifacts.store: silent exception", exc_info=True)

    from storage.events import publish
    publish(workspace_id, "artifact", "created", art_id)

    return rec


def get_artifact(workspace_id: str, artifact_id: str) -> Optional[ArtifactRecord]:
    """Get artifact by exact ID (not sha256)."""
    from storage.ids import validate_workspace_id
    workspace_id = validate_workspace_id(workspace_id)
    for data in reversed(_read_artifact_record_dicts(workspace_id)):
        if data.get("artifact_id") == artifact_id:
            try:
                return _artifact_record_from_dict(data)
            except Exception:
                return None
    return None


def read_artifact_content(workspace_id: str, artifact_id: str,
                          allow_sensitive: bool = False) -> Optional[str]:
    """Read artifact file content. Secret always blocked."""
    rec = get_artifact(workspace_id, artifact_id)
    if not rec:
        return None
    if rec.sensitivity == "secret":
        return None
    if rec.sensitivity == "sensitive" and not allow_sensitive:
        return None
    if rec.lifecycle == "deleted":
        return None

    file_id = getattr(rec, "file_id", "") or (rec.metadata or {}).get("file_id", "")
    if file_id:
        try:
            from storage.file_store import read_file_content
            return read_file_content(workspace_id, file_id)
        except Exception:
            _LOG.warning("artifacts.store: silent exception", exc_info=True)

    return None


def list_artifacts(workspace_id: str, run_id: str = None, artifact_type: str = None,
                   scope: str = None, sensitivity: str = None,
                   include_deleted: bool = False, limit: int = 100,
                   evidence_view: str = "", producer_id: str = "",
                   asset_id: str = "") -> list:
    all_active_records: list[ArtifactRecord] = []
    records: list[ArtifactRecord] = []
    for rec in _records_in_index_order(workspace_id):
        if not include_deleted and rec.lifecycle == "deleted":
            continue
        if rec.lifecycle != "deleted":
            all_active_records.append(rec)
        if run_id and rec.run_id != run_id:
            continue
        if artifact_type and rec.artifact_type != artifact_type:
            continue
        if scope and rec.scope != scope:
            continue
        if sensitivity and rec.sensitivity != sensitivity:
            continue
        records.append(rec)

    from artifacts.governance import build_governance
    # Authority must be computed from every active version in the workspace,
    # even when this list request filters to one run or artifact type.
    governance = build_governance(all_active_records)
    results = []
    # Preserve the store/index presentation order after report deduplication.
    # Authority projection is independent of display order, and callers rely
    # on this stable ordering for report history.
    display_records = _dedupe_artifacts_for_listing(records)
    for rec in display_records:
        metadata = rec.metadata if isinstance(rec.metadata, dict) else {}
        projection = governance.get(rec.artifact_id)
        if evidence_view == "current" and (projection or {}).get("authority_status") not in {"authoritative", "provisional"}:
            continue
        if evidence_view == "history" and (projection or {}).get("authority_status") not in {"historical", "incomplete"}:
            continue
        if evidence_view == "deliverables" and projection:
            continue
        if producer_id and str(metadata.get("producer_id", "")) != producer_id:
            continue
        if asset_id and str(metadata.get("asset_id", "")) != asset_id:
            continue
        item = sanitize_record(rec, include_metadata=True)
        item["governance"] = projection or {
            "authority_status": "not_applicable",
            "authority_reason": "该制品是交付物或派生材料，不参与原始证据权威选择",
        }
        results.append(item)
        if len(results) >= limit:
            break
    return results


def get_artifact_governance(workspace_id: str, artifact_id: str) -> dict:
    """Return the authority projection using every active artifact version."""
    from artifacts.governance import build_governance

    records = [rec for rec in _records_in_index_order(workspace_id) if rec.lifecycle != "deleted"]
    return build_governance(records).get(artifact_id, {
        "authority_status": "not_applicable",
        "authority_reason": "该制品是交付物或派生材料，不参与原始证据权威选择",
    })


def artifact_governance_summary(workspace_id: str) -> dict:
    from artifacts.governance import governance_summary

    records = [rec for rec in _records_in_index_order(workspace_id) if rec.lifecycle != "deleted"]
    return governance_summary(records)


def update_artifact_tags(workspace_id: str, artifact_id: str, tags: list) -> bool:
    rec = get_artifact(workspace_id, artifact_id)
    if not rec:
        return False
    rec.tags = list(tags or [])
    rec.updated_at = now_iso()
    _save_artifact_record(rec)
    from storage.events import publish
    publish(workspace_id, "artifact", "updated", artifact_id)
    return True


def delete_artifact(workspace_id: str, artifact_id: str, hard: bool = False) -> bool:
    rec = get_artifact(workspace_id, artifact_id)
    if not rec:
        return False
    _remove_from_knowledge_index(workspace_id, artifact_id)
    if hard:
        records = _records_in_index_order(workspace_id)
        file_is_shared = bool(rec.file_id) and any(
            item.artifact_id != artifact_id
            and item.lifecycle != "deleted"
            and item.file_id == rec.file_id
            for item in records
        )
        if rec.file_id and not file_is_shared:
            from storage.file_store import delete_file_permanently
            delete_file_permanently(workspace_id, rec.file_id)
            from storage.reference_index import list_references_for_file, remove_reference
            for reference in list_references_for_file(workspace_id, rec.file_id):
                remove_reference(workspace_id, reference.get("ref_id", ""))
        elif not rec.file_id:
            relative_path = str(rec.relative_path or "")
            if relative_path and not any(
                item.artifact_id != artifact_id
                and item.lifecycle != "deleted"
                and item.relative_path == relative_path
                for item in records
            ):
                scoped_root = _workspace_path(workspace_id).resolve()
                candidate = (scoped_root / relative_path).resolve()
                try:
                    candidate.relative_to(scoped_root)
                    candidate.unlink(missing_ok=True)
                except (OSError, ValueError):
                    _LOG.warning("artifact payload cleanup failed: %s", candidate, exc_info=True)
        from storage.reference_index import list_references_for_owner, remove_reference
        for reference in list_references_for_owner(workspace_id, "artifact", artifact_id):
            remove_reference(workspace_id, reference.get("ref_id", ""))
        _remove_artifact_record_permanently(workspace_id, artifact_id)
        _remove_artifact_from_run_indexes(workspace_id, artifact_id)
    else:
        rec.lifecycle = "deleted"
        rec.updated_at = now_iso()
        _save_artifact_record(rec)
        if rec.file_id:
            from storage.file_store import soft_delete_file
            soft_delete_file(workspace_id, rec.file_id)
    from storage.events import publish
    publish(workspace_id, "artifact", "deleted", artifact_id)
    return True


def _remove_artifact_record_permanently(workspace_id: str, artifact_id: str) -> None:
    """Remove artifact metadata and its lightweight index entry."""
    from storage.artifact_metadata_store import remove_artifact_record

    remove_artifact_record(workspace_id, artifact_id)


def _remove_artifact_from_run_indexes(workspace_id: str, artifact_id: str) -> None:
    from storage.run_artifact_store import remove_artifact_from_all_runs

    remove_artifact_from_all_runs(workspace_id, artifact_id)


def _remove_from_knowledge_index(workspace_id: str, artifact_id: str):
    """Remove knowledge source entries for a deleted artifact."""
    try:
        from agent.modules.knowledge.service import delete_source, list_sources
        from storage.ids import validate_workspace_id
        validate_workspace_id(workspace_id)
        result = list_sources(
            workspace_id, include_disabled=True, include_deleted=True,
        )
        for source in result.get("sources", []):
            metadata = source.get("metadata") or {}
            if metadata.get("artifact_id") == artifact_id:
                delete_source(workspace_id, source.get("source_id", ""))
    except Exception:
        pass  # Best-effort cleanup, don't block deletion


def promote_artifact(workspace_id: str, artifact_id: str, target_scope: str) -> Optional[ArtifactRecord]:
    if target_scope not in ("workspace", "shared", "global"):
        return None
    rec = get_artifact(workspace_id, artifact_id)
    if not rec:
        return None
    if rec.sensitivity == "secret" and target_scope == "shared":
        return None
    if rec.sensitivity == "sensitive" and target_scope == "shared" \
            and not rec.metadata.get("_explicit_allow_sensitive_share"):
        return None
    rec.scope = target_scope
    rec.lifecycle = "promoted"
    rec.updated_at = now_iso()
    _save_artifact_record(rec)
    from storage.events import publish
    publish(workspace_id, "artifact", "updated", artifact_id)
    return rec


def summarize_artifact_content(workspace_id: str, artifact_id: str) -> dict:
    rec = get_artifact(workspace_id, artifact_id)
    if not rec:
        return {}
    return {"artifact_id": rec.artifact_id, "artifact_type": rec.artifact_type,
            "title": rec.title, "size_bytes": rec.size_bytes,
            "sensitivity": rec.sensitivity, "summary": rec.summary,
            "created_at": rec.created_at, "sha256_short": rec.sha256[:12] if rec.sha256 else ""}


def get_run_artifacts(workspace_id: str, run_id: str) -> dict:
    from storage.ids import validate_workspace_id
    from storage.run_artifact_store import read_run_artifacts
    workspace_id = validate_workspace_id(workspace_id)
    return read_run_artifacts(workspace_id, run_id) or {"workspace_id": workspace_id, "run_id": run_id,
            "input_artifacts": [], "output_artifacts": [], "report_artifacts": [], "temp_artifacts": []}


def sanitize_record(rec: ArtifactRecord, include_metadata: bool = False) -> dict:
    """Return safe dict for API — no absolute path, metadata redacted."""
    if not isinstance(rec, ArtifactRecord):
        return rec
    d = {
        "artifact_id": rec.artifact_id, "workspace_id": rec.workspace_id,
        "run_id": rec.run_id, "module": rec.module, "skill": rec.skill,
        "capability_id": rec.capability_id, "artifact_type": rec.artifact_type,
        "title": rec.title, "summary": rec.summary,
        "scope": rec.scope, "sensitivity": rec.sensitivity,
        "lifecycle": rec.lifecycle, "relative_path": rec.relative_path,
        "mime_type": rec.mime_type, "file_ext": rec.file_ext,
        "size_bytes": rec.size_bytes, "sha256_short": rec.sha256[:12] if rec.sha256 else "",
        "file_id": rec.file_id,
        "created_by": rec.created_by,
        "source": rec.source, "created_at": rec.created_at, "updated_at": rec.updated_at,
        "tags": rec.tags, "redaction_applied": rec.redaction_applied,
    }
    if include_metadata:
        d["metadata"] = redact_metadata(rec.metadata or {})
    return d


# ── helpers ──

def _update_run_index(ws_id, run_id, art_id, artifact_type, title):
    from storage.ids import validate_workspace_id
    from storage.run_artifact_store import mutate_run_artifacts
    ws_id = validate_workspace_id(ws_id)
    def _append(idx):
        info = {"artifact_id": art_id, "artifact_type": artifact_type, "title": title}
        if artifact_type in ("input_data", "template", "sample"):
            idx.setdefault("input_artifacts", []).append(info)
        elif artifact_type in ("output_data",):
            idx.setdefault("output_artifacts", []).append(info)
        elif artifact_type in ("report",):
            idx.setdefault("report_artifacts", []).append(info)
        else:
            idx.setdefault("temp_artifacts", []).append(info)
    mutate_run_artifacts(ws_id, run_id, _append)


def _type_dir(artifact_type: str) -> str:
    m = {"input_data": "inputs", "output_data": "outputs", "report": "reports",
         "knowledge_doc": "knowledge", "knowledge_index": "knowledge",
         "template": "inputs", "sample": "inputs", "trace_export": "reports",
         "temp": "temp", "unknown": "quarantine"}
    return m.get(artifact_type, "quarantine")


def _all_type_dirs() -> list:
    return ["inputs", "outputs", "reports", "topology", "knowledge", "temp", "quarantine"]
