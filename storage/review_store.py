"""Durable storage helpers for user-visible review items."""
from __future__ import annotations

from typing import Any

from storage.records import atomic_save_json, read_json_record, workspace_record_dir, workspace_record_file
from storage.locking import FileLock
from storage.time_utils import now_iso


def load_sidecar(workspace_id: str, artifact_id: str) -> dict[str, Any] | None:
    return read_json_record(workspace_id, _sidecar_parts(artifact_id))


def save_sidecar(workspace_id: str, artifact_id: str, value: dict[str, Any]) -> None:
    atomic_save_json(workspace_id, _sidecar_parts(artifact_id), value)


def mutate_sidecar(workspace_id: str, artifact_id: str, mutate):
    """Atomically read, mutate and persist one review sidecar."""
    parts = _sidecar_parts(artifact_id)
    path = workspace_record_file(workspace_id, *parts)
    with FileLock(path.with_name(path.name + ".lock")):
        sidecar = read_json_record(workspace_id, parts) or {}
        result = mutate(sidecar)
        atomic_save_json(workspace_id, parts, sidecar)
        return result


def list_review_artifact_ids(workspace_id: str) -> list[str]:
    """Return every review sidecar id, including user-created manual queues."""
    root = workspace_record_dir(workspace_id, "sys", "reviews", create=False)
    if not root.is_dir():
        return []
    return sorted(path.stem for path in root.glob("*.json") if path.stem)


def append_review_item(workspace_id: str, artifact_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Append one item atomically, de-duplicating stable source keys."""
    def mutate(sidecar: dict[str, Any]) -> dict[str, Any]:
        raw = sidecar.get("items")
        if not isinstance(raw, list):
            raw = sidecar.get("review_items")
        if not isinstance(raw, list):
            raw = sidecar.get("manual_review")
        items = [dict(value) for value in raw if isinstance(value, dict)] if isinstance(raw, list) else []
        source_key = str(item.get("source_key") or "").strip()
        if source_key:
            for existing in items:
                if str(existing.get("source_key") or "") == source_key:
                    return dict(existing)
        saved = dict(item)
        items.append(saved)
        sidecar.update({
            "workspace_id": workspace_id,
            "artifact_id": artifact_id,
            "items": items,
            "updated_at": now_iso(),
        })
        return saved
    return mutate_sidecar(workspace_id, artifact_id, mutate)


def _sidecar_parts(artifact_id: str) -> tuple[str, ...]:
    clean = str(artifact_id or "").strip()
    if not clean or len(clean) > 128 or ".." in clean or "/" in clean or "\\" in clean:
        raise ValueError(f"invalid artifact_id: {artifact_id!r}")
    return ("sys", "reviews", f"{clean}.json")


def record_workflow_failure_review(record: dict[str, Any]) -> None:
    """Surface a failed canonical workflow run in the durable review inbox.

    This side effect cannot alter the run result. `source_key` makes repeated
    resume/finalize calls idempotent for the same run.
    """
    if str(record.get("status") or "") != "failed":
        return
    workspace_id = str(record.get("workspace_id") or "")
    run_id = str(record.get("run_id") or "")
    workflow_id = str(record.get("workflow_id") or "")
    if not workspace_id or not run_id:
        return
    failed_nodes = [node for node in record.get("nodes") or [] if isinstance(node, dict) and node.get("status") == "failed"]
    detail = "; ".join(str(node.get("summary") or node.get("tool_id") or node.get("node_id") or "步骤失败") for node in failed_nodes[:3])
    append_review_item(workspace_id, f"workflow-{run_id}", {
        "item_id": f"review_workflow_{run_id}",
        "title": f"流程“{workflow_id or run_id}”运行失败",
        "reason": detail or "流程运行失败，需要人工检查步骤输入、权限或目标系统状态。",
        "severity": "error",
        "category": "workflow_failure",
        "source_type": "workflow_run",
        "source_key": f"workflow-run:{run_id}",
        "requires_human_review": True,
        "status": "pending",
        "created_at": str(record.get("finished_at") or now_iso()),
    })
