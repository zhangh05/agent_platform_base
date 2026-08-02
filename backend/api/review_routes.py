# backend/api/review_routes.py
"""Review HTTP routes — thin wrappers around the v0.9 review module.

Endpoints:
  GET  /api/workspaces/<ws_id>/review-items        - workspace-level list
  PUT  /api/review-items/<item_id>                  - update one item (artifact from query)
  GET  /api/workspaces/<ws_id>/artifacts/<art_id>/review-items
                                                  - artifact-scoped list (per-art)

No new tool is added. These endpoints proxy the existing
agent.modules.review.service.* functions, which are wired into the canonical
review actions. Tool count remains unchanged.

Note: PUT /api/review-items/<item_id> requires ?workspace_id=&artifact_id=
query parameters, because review items are scoped per-artifact via the
sidecar storage layout.
"""

import hashlib
from typing import Any

from flask import jsonify, request

from storage.ids import validate_workspace_id
from storage.time_utils import now_iso


def _invalid_ws():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _validated_ws_id(raw="default"):
    try:
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_ws()


def _list_artifacts_for_workspace(workspace_id: str) -> list:
    """Enumerate artifact_ids for a workspace by scanning the store."""
    try:
        from artifacts.store import list_artifacts
        arts = list_artifacts(workspace_id) or []
        return [a.get("artifact_id", "") if isinstance(a, dict) else
                getattr(a, "artifact_id", "")
                for a in arts if (isinstance(a, dict) and a.get("artifact_id")) or
                                 (not isinstance(a, dict) and getattr(a, "artifact_id", None))]
    except Exception:
        return []


def _sidecar_items(sidecar: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(sidecar, dict):
        return []
    raw = sidecar.get("items")
    if raw is None:
        raw = sidecar.get("review_items")
    if raw is None:
        raw = sidecar.get("manual_review")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _with_defaults(workspace_id: str, artifact_id: str, item: dict[str, Any]) -> dict[str, Any]:
    now = now_iso()
    item_id = str(item.get("item_id") or item.get("id") or "").strip()
    if not item_id:
        digest = hashlib.sha1(f"{artifact_id}:{item}".encode("utf-8", errors="replace")).hexdigest()[:12]
        item_id = f"review_{digest}"
    status = str(item.get("status") or "pending").strip().lower()
    if status not in {"pending", "accepted", "ignored", "modified"}:
        status = "pending"
    severity = str(item.get("severity") or "warning").strip().lower()
    if severity not in {"info", "warning", "error"}:
        severity = "warning"
    return {
        **item,
        "item_id": item_id,
        "workspace_id": workspace_id,
        "artifact_id": artifact_id,
        "severity": severity,
        "category": str(item.get("category") or item.get("type") or "manual_review"),
        "reason": str(item.get("reason") or item.get("message") or item.get("summary") or "需要人工确认"),
        "requires_human_review": bool(item.get("requires_human_review", True)),
        "status": status,
        "user_note": str(item.get("user_note") or ""),
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


def _update_review_item(workspace_id: str, artifact_id: str, item_id: str, status: str, user_note: str) -> dict[str, Any]:
    if status not in {"pending", "accepted", "ignored", "modified"}:
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
        normalized.update({"status": status, "user_note": user_note, "updated_at": now_iso()})
        items[index] = normalized
        updated = normalized
    if updated is None:
        return {"ok": False, "errors": ["item_not_found"]}
    sidecar[key] = items
    sidecar["updated_at"] = now_iso()
    save_sidecar(workspace_id, artifact_id, sidecar)
    return {"ok": True, "item": updated}


def register_review_routes(app):
    """Register review HTTP routes on the Flask app."""

    @app.route("/api/workspaces/<ws_id>/review-items")
    def api_workspace_review_items(ws_id):
        """Workspace-level review item list (aggregated across artifacts)."""
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        status = request.args.get("status")

        artifact_ids = _list_artifacts_for_workspace(ws_id)
        aggregated = []
        for art_id in artifact_ids:
            res = _list_review_items(ws_id, art_id)
            if not res.get("ok"):
                continue
            for it in res.get("items", []):
                if status and it.get("status") != status:
                    continue
                it["artifact_id"] = art_id  # attach artifact context for frontend
                aggregated.append(it)
        return jsonify({
            "ok": True,
            "items": aggregated,
            "count": len(aggregated),
            "workspace_id": ws_id,
        })

    @app.route("/api/workspaces/<ws_id>/artifacts/<artifact_id>/review-items")
    def api_artifact_review_items(ws_id, artifact_id):
        """Artifact-scoped review item list."""
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        return jsonify(_list_review_items(ws_id, artifact_id))

    @app.route("/api/review-items/<item_id>", methods=["PUT"])
    def api_review_item_update(item_id):
        """Update a single review item. Requires ?workspace_id and ?artifact_id."""
        ws_id = request.args.get("workspace_id", "")
        artifact_id = request.args.get("artifact_id", "")
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        if not artifact_id:
            return jsonify({"ok": False, "error": "artifact_id required"}), 400
        data = request.get_json(silent=True) or {}
        status = data.get("status")
        user_note = data.get("user_note", "")
        if not status:
            return jsonify({"ok": False, "error": "status required"}), 400
        res = _update_review_item(ws_id, artifact_id, item_id, status, user_note)
        # The service returns "ok": False for not_found — surface 4xx instead of 200.
        if not res.get("ok"):
            err = (res.get("errors") or ["unknown_error"])[0]
            code = 404 if err == "artifact_not_found" or err == "item_not_found" else 400
            return jsonify(res), code
        return jsonify(res)
