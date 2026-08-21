# backend/api/review_routes.py
"""Workspace-scoped review inbox APIs.

The inbox stores user-initiated and runtime-produced review items as durable
sidecars. It never changes source artifacts or bypasses the canonical runtime.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from flask import jsonify, request

from storage.ids import validate_workspace_id
from storage.time_utils import now_iso

_REVIEW_STATUSES = {"pending", "accepted", "ignored", "modified"}
_SEVERITIES = {"info", "warning", "error"}


def _invalid_ws():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _validated_ws_id(raw="default"):
    try:
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_ws()


def _list_artifacts_for_workspace(workspace_id: str) -> list[str]:
    """Union durable review sidecars with registered artifact records."""
    artifact_ids: set[str] = set()
    try:
        from artifacts.store import list_artifacts
        for artifact in list_artifacts(workspace_id) or []:
            artifact_id = artifact.get("artifact_id", "") if isinstance(artifact, dict) else getattr(artifact, "artifact_id", "")
            if artifact_id:
                artifact_ids.add(str(artifact_id))
    except Exception:
        pass
    try:
        from storage.review_store import list_review_artifact_ids
        artifact_ids.update(list_review_artifact_ids(workspace_id))
    except (OSError, ValueError):
        pass
    return sorted(artifact_ids)


def _sidecar_items(sidecar: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(sidecar, dict):
        return []
    raw = sidecar.get("items")
    if raw is None:
        raw = sidecar.get("review_items")
    if raw is None:
        raw = sidecar.get("manual_review")
    return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _with_defaults(workspace_id: str, artifact_id: str, item: dict[str, Any]) -> dict[str, Any]:
    now = now_iso()
    item_id = str(item.get("item_id") or item.get("id") or "").strip()
    if not item_id:
        digest = hashlib.sha1(f"{artifact_id}:{item}".encode("utf-8", errors="replace")).hexdigest()[:12]
        item_id = f"review_{digest}"
    status = str(item.get("status") or "pending").strip().lower()
    severity = str(item.get("severity") or "warning").strip().lower()
    return {
        **item,
        "item_id": item_id,
        "workspace_id": workspace_id,
        "artifact_id": artifact_id,
        "title": str(item.get("title") or item.get("category") or "人工复核")[:160],
        "severity": severity if severity in _SEVERITIES else "warning",
        "category": str(item.get("category") or item.get("type") or "manual_review")[:80],
        "reason": str(item.get("reason") or item.get("message") or item.get("summary") or "需要人工确认")[:4000],
        "requires_human_review": bool(item.get("requires_human_review", True)),
        "status": status if status in _REVIEW_STATUSES else "pending",
        "user_note": str(item.get("user_note") or "")[:4000],
        "created_at": str(item.get("created_at") or now),
        "updated_at": str(item.get("updated_at") or now),
    }


def _list_review_items(workspace_id: str, artifact_id: str) -> dict[str, Any]:
    try:
        from storage.review_store import load_sidecar
        sidecar = load_sidecar(workspace_id, artifact_id)
    except (OSError, ValueError):
        return {"ok": False, "errors": ["review_sidecar_unreadable"], "items": []}
    items = [_with_defaults(workspace_id, artifact_id, item) for item in _sidecar_items(sidecar)]
    return {"ok": True, "items": items, "count": len(items), "workspace_id": workspace_id, "artifact_id": artifact_id}


def _create_review_item(workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not title or not reason:
        return {"ok": False, "errors": ["title_and_reason_required"]}
    artifact_id = str(payload.get("artifact_id") or "manual-review").strip() or "manual-review"
    severity = str(payload.get("severity") or "warning").strip().lower()
    if severity not in _SEVERITIES:
        return {"ok": False, "errors": ["invalid_severity"]}
    item = _with_defaults(workspace_id, artifact_id, {
        "item_id": f"review_{uuid.uuid4().hex[:16]}",
        "title": title,
        "reason": reason,
        "severity": severity,
        "category": str(payload.get("category") or "manual_review").strip() or "manual_review",
        "source_type": "manual",
        "status": "pending",
        "created_at": now_iso(),
    })
    try:
        from storage.review_store import append_review_item
        append_review_item(workspace_id, artifact_id, item)
    except (OSError, ValueError):
        return {"ok": False, "errors": ["review_sidecar_unwritable"]}
    return {"ok": True, "item": item}


def _update_review_item(workspace_id: str, artifact_id: str, item_id: str, status: str, user_note: str) -> dict[str, Any]:
    if status not in _REVIEW_STATUSES:
        return {"ok": False, "errors": ["invalid_status"]}
    try:
        from storage.review_store import load_sidecar, save_sidecar
        sidecar = load_sidecar(workspace_id, artifact_id)
    except (OSError, ValueError):
        return {"ok": False, "errors": ["artifact_not_found"]}
    if not isinstance(sidecar, dict):
        return {"ok": False, "errors": ["artifact_not_found"]}
    key = "items" if isinstance(sidecar.get("items"), list) else (
        "review_items" if isinstance(sidecar.get("review_items"), list) else (
            "manual_review" if isinstance(sidecar.get("manual_review"), list) else "items"
        )
    )
    items = _sidecar_items(sidecar)
    updated = None
    for index, item in enumerate(items):
        normalized = _with_defaults(workspace_id, artifact_id, item)
        if normalized["item_id"] != item_id:
            items[index] = normalized
            continue
        normalized.update({"status": status, "user_note": str(user_note or "")[:4000], "updated_at": now_iso()})
        items[index] = normalized
        updated = normalized
    if updated is None:
        return {"ok": False, "errors": ["item_not_found"]}
    sidecar[key] = items
    sidecar["updated_at"] = now_iso()
    save_sidecar(workspace_id, artifact_id, sidecar)
    return {"ok": True, "item": updated}


def register_review_routes(app):
    """Register workspace-scoped review inbox routes."""

    @app.route("/api/workspaces/<ws_id>/review-items", methods=["GET", "POST"])
    def api_workspace_review_items(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        if request.method == "POST":
            result = _create_review_item(ws_id, request.get_json(silent=True) or {})
            if not result.get("ok"):
                return jsonify(result), 400
            return jsonify(result), 201
        status = request.args.get("status")
        if status and status not in _REVIEW_STATUSES:
            return jsonify({"ok": False, "error": "invalid_status"}), 400
        aggregated = []
        for artifact_id in _list_artifacts_for_workspace(ws_id):
            result = _list_review_items(ws_id, artifact_id)
            if not result.get("ok"):
                continue
            for item in result.get("items", []):
                if status and item.get("status") != status:
                    continue
                aggregated.append(item)
        aggregated.sort(key=lambda item: (item.get("status") != "pending", item.get("updated_at") or ""), reverse=False)
        return jsonify({"ok": True, "items": aggregated, "count": len(aggregated), "workspace_id": ws_id})

    @app.route("/api/workspaces/<ws_id>/artifacts/<artifact_id>/review-items")
    def api_artifact_review_items(ws_id, artifact_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        return jsonify(_list_review_items(ws_id, artifact_id))

    @app.route("/api/review-items/<item_id>", methods=["PUT"])
    def api_review_item_update(item_id):
        ws_id, err = _validated_ws_id(request.args.get("workspace_id", ""))
        if err:
            return err
        artifact_id = str(request.args.get("artifact_id") or "")
        if not artifact_id:
            return jsonify({"ok": False, "error": "artifact_id required"}), 400
        data = request.get_json(silent=True) or {}
        status = data.get("status")
        if not status:
            return jsonify({"ok": False, "error": "status required"}), 400
        result = _update_review_item(ws_id, artifact_id, item_id, status, data.get("user_note", ""))
        if not result.get("ok"):
            error = (result.get("errors") or ["unknown_error"])[0]
            code = 404 if error in {"artifact_not_found", "item_not_found"} else 400
            return jsonify(result), code
        return jsonify(result)
