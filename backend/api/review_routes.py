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

from flask import jsonify, request

from storage.ids import validate_workspace_id


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


def register_review_routes(app):
    """Register review HTTP routes on the Flask app."""

    @app.route("/api/workspaces/<ws_id>/review-items")
    def api_workspace_review_items(ws_id):
        """Workspace-level review item list (aggregated across artifacts)."""
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        status = request.args.get("status")

        from agent.modules.review.service import list_review_items
        artifact_ids = _list_artifacts_for_workspace(ws_id)
        aggregated = []
        for art_id in artifact_ids:
            res = list_review_items(ws_id, art_id)
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
        from agent.modules.review.service import list_review_items
        return jsonify(list_review_items(ws_id, artifact_id))

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
        from agent.modules.review.service import update_review_item
        res = update_review_item(ws_id, artifact_id, item_id, status, user_note)
        # The service returns "ok": False for not_found — surface 4xx instead of 200.
        if not res.get("ok"):
            err = (res.get("errors") or ["unknown_error"])[0]
            code = 404 if err == "artifact_not_found" or err == "item_not_found" else 400
            return jsonify(res), code
        return jsonify(res)
