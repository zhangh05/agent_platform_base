"""Read models for the user-facing workspace data center."""

from __future__ import annotations

from collections import Counter
from typing import Any

from storage.artifact_metadata_store import list_artifact_records
from storage.file_store import get_file_record, list_files, read_file_content
from storage.gc import gc_preview
from storage.ids import validate_workspace_id
from storage.reference_index import list_references_for_file


def data_overview(workspace_id: str) -> dict[str, Any]:
    ws_id = validate_workspace_id(workspace_id)
    files = list_files(ws_id, lifecycle="")
    artifacts = list_artifact_records(ws_id)
    active_files = [item for item in files if item.get("lifecycle", "active") == "active"]
    active_artifacts = [item for item in artifacts if item.get("lifecycle", "active") == "active"]
    lifecycle_counts = Counter(str(item.get("lifecycle") or "active") for item in files)
    type_counts = Counter(str(item.get("logical_type") or "unknown") for item in active_files)
    health = gc_preview(ws_id)
    referenced = 0
    unreferenced = 0
    for item in active_files:
        if list_references_for_file(ws_id, str(item.get("file_id") or "")):
            referenced += 1
        else:
            unreferenced += 1
    return {
        "workspace_id": ws_id,
        "files": {
            "total": len(files),
            "active": len(active_files),
            "archived": lifecycle_counts.get("archived", 0),
            "soft_deleted": lifecycle_counts.get("soft_deleted", 0),
            "size_bytes": sum(int(item.get("size_bytes") or 0) for item in active_files),
            "referenced": referenced,
            "unreferenced": unreferenced,
        },
        "artifacts": {
            "total": len(artifacts),
            "active": len(active_artifacts),
        },
        "types": dict(type_counts),
        "health": {
            "orphan_files": len(health.get("orphan_files") or []),
            "missing_on_disk": len(health.get("missing_on_disk") or []),
            "soft_deleted": len(health.get("soft_deleted") or []),
            "ok": not any(health.get(key) for key in ("orphan_files", "missing_on_disk")),
        },
    }


def managed_data_files(
    workspace_id: str,
    *,
    logical_type: str = "",
    lifecycle: str = "active",
) -> list[dict[str, Any]]:
    ws_id = validate_workspace_id(workspace_id)
    files = list_files(ws_id, logical_type=logical_type, lifecycle=lifecycle)
    artifacts_by_file: dict[str, list[dict[str, Any]]] = {}
    for artifact in list_artifact_records(ws_id):
        file_id = str(artifact.get("file_id") or "")
        if not file_id:
            continue
        artifacts_by_file.setdefault(file_id, []).append({
            "artifact_id": artifact.get("artifact_id", ""),
            "artifact_type": artifact.get("artifact_type", ""),
            "title": artifact.get("title", ""),
            "lifecycle": artifact.get("lifecycle", "active"),
            "run_id": artifact.get("run_id", ""),
            "authority": (artifact.get("metadata") or {}).get("authority", ""),
        })
    result: list[dict[str, Any]] = []
    for record in files:
        file_id = str(record.get("file_id") or "")
        references = list_references_for_file(ws_id, file_id)
        metadata = dict(record.get("metadata") or {})
        result.append({
            "file_id": file_id,
            "logical_type": record.get("logical_type", ""),
            "file_kind": record.get("file_kind", ""),
            "original_name": record.get("original_name", ""),
            "mime_type": record.get("mime_type", ""),
            "binary": bool(record.get("binary", False)),
            "size_bytes": int(record.get("size_bytes") or 0),
            "created_at": record.get("created_at", ""),
            "source": record.get("source", ""),
            "sensitivity": record.get("sensitivity", "internal"),
            "lifecycle": record.get("lifecycle", "active"),
            "session_id": record.get("session_id", ""),
            "run_id": record.get("run_id", ""),
            "metadata": metadata,
            "artifacts": artifacts_by_file.get(file_id, []),
            "reference_count": len(references),
            "reference_types": sorted({str(item.get("owner_type") or "") for item in references if item.get("owner_type")}),
            "references": [{
                "owner_type": str(item.get("owner_type") or ""),
                "owner_id": str(item.get("owner_id") or ""),
                "relation": str(item.get("relation") or "source"),
            } for item in references],
        })
    result.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return result


def file_relations(workspace_id: str, file_id: str) -> dict[str, Any] | None:
    ws_id = validate_workspace_id(workspace_id)
    record = get_file_record(ws_id, file_id)
    if record is None:
        return None
    artifacts = [
        item for item in list_artifact_records(ws_id)
        if item.get("file_id") == file_id
    ]
    references = list_references_for_file(ws_id, file_id)
    return {
        "file_id": file_id,
        "artifacts": [{
            "artifact_id": item.get("artifact_id", ""),
            "artifact_type": item.get("artifact_type", ""),
            "title": item.get("title", ""),
            "lifecycle": item.get("lifecycle", "active"),
            "run_id": item.get("run_id", ""),
        } for item in artifacts],
        "references": references,
        "in_use": bool(artifacts or references),
    }


def text_file_content(workspace_id: str, file_id: str, *, max_chars: int = 100_000) -> dict[str, Any] | None:
    ws_id = validate_workspace_id(workspace_id)
    record = get_file_record(ws_id, file_id)
    if record is None:
        return None
    if record.get("binary"):
        return {"file_id": file_id, "binary": True, "content": "", "truncated": False}
    content = read_file_content(ws_id, file_id)
    return {
        "file_id": file_id,
        "binary": False,
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
    }


def delete_unreferenced_file(workspace_id: str, file_id: str, *, force: bool = False) -> dict[str, Any]:
    """Delete a managed file.

    The default path stays conservative for backend callers: referenced files
    are refused. User-facing data management can pass force=True after explicit
    confirmation to remove the file, its direct references and associated
    artifact records together.
    """
    ws_id = validate_workspace_id(workspace_id)
    relations = file_relations(ws_id, file_id)
    if relations is None:
        return {"ok": False, "error": "file_not_found"}
    if relations["in_use"] and not force:
        return {
            "ok": False,
            "error": "file_in_use",
            "relations": relations,
        }
    if force:
        from artifacts.store import delete_artifact
        for artifact in relations.get("artifacts") or []:
            artifact_id = str(artifact.get("artifact_id") or "")
            if artifact_id:
                delete_artifact(ws_id, artifact_id, hard=True)
        from storage.reference_index import list_references_for_file, remove_reference
        for reference in list_references_for_file(ws_id, file_id):
            remove_reference(ws_id, str(reference.get("ref_id") or ""))
        relations = file_relations(ws_id, file_id)
        if relations is None:
            from storage.events import publish
            publish(ws_id, "file", "deleted", file_id)
            return {"ok": True, "file_id": file_id}
    from storage.file_store import delete_file_permanently
    if not delete_file_permanently(ws_id, file_id):
        return {"ok": False, "error": "delete_failed"}
    from storage.events import publish
    publish(ws_id, "file", "deleted", file_id)
    return {"ok": True, "file_id": file_id}
