# backend/api/artifact_routes.py
"""Artifact API routes — CRUD for artifact store."""

from flask import jsonify, request
from storage.ids import validate_workspace_id
from artifacts.store import (
    save_artifact, sanitize_record,
    list_artifacts, get_artifact, read_artifact_content,
    delete_artifact, promote_artifact, summarize_artifact_content,
    get_run_artifacts, get_artifact_governance, artifact_governance_summary,
    _get_max_size, _get_ws_root,
)


def _invalid_ws():
    return jsonify({"ok": False, "error": "invalid_workspace_id"}), 400


def _invalid_limit():
    return jsonify({"ok": False, "error": "invalid_limit"}), 400


def _validated_ws_id(raw="default"):
    try:
        return validate_workspace_id(raw), None
    except ValueError:
        return None, _invalid_ws()


def _validated_limit(default=100, max_value=500):
    from backend.core.params import parse_limit
    try:
        return parse_limit(request.args, default=default, max_value=max_value), None
    except ValueError:
        return None, _invalid_limit()


def _confirm_required():
    return jsonify({
        "ok": False,
        "error": "confirm_required",
        "message": "Set confirm=true to delete artifacts.",
    }), 400


def _confirmed(data: dict | None = None) -> bool:
    if request.args.get("confirm", "") == "true":
        return True
    if isinstance(data, dict) and data.get("confirm") is True:
        return True
    return False


def _guess_upload_kind(filename: str, artifact_type: str = "") -> tuple:
    """Return (file_kind, binary) for uploaded file."""
    name = (filename or "").lower()
    at = (artifact_type or "").lower()
    if name.endswith(".pdf"):
        return "pdf", True
    if name.endswith(".docx"):
        return "docx", True
    if name.endswith(".xlsx"):
        return "xlsx", True
    if name.endswith(".pptx"):
        return "pptx", True
    if name.endswith((".zip", ".tar", ".gz", ".7z")):
        return "zip", True
    if name.endswith((".json",)):
        return "json", False
    if name.endswith((".yaml", ".yml")):
        return "yaml", False
    if name.endswith((".md",)):
        return "markdown", False
    if name.endswith((".cfg", ".conf", ".txt", ".log")) or at in ("config", "config_input"):
        return "config", False
    return "text", False


def register_artifact_routes(app):
    """Register all artifact API routes on the Flask app."""

    @app.route("/api/workspaces/<ws_id>/artifacts")
    def api_workspace_artifacts(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        run_id = request.args.get("run_id")
        art_type = request.args.get("artifact_type")
        scope = request.args.get("scope")
        sens = request.args.get("sensitivity")
        evidence_view = request.args.get("evidence_view", "")
        if evidence_view not in {"", "current", "history", "deliverables"}:
            return jsonify({"ok": False, "error": "invalid_evidence_view"}), 400
        inc_del = request.args.get("include_deleted", "0") == "1"
        lim, err = _validated_limit(default=100, max_value=500)
        if err:
            return err
        return jsonify({
            "artifacts": list_artifacts(
                ws_id, run_id=run_id, artifact_type=art_type, scope=scope,
                sensitivity=sens, include_deleted=inc_del, limit=lim,
                evidence_view=evidence_view,
                producer_id=request.args.get("producer_id", ""),
                asset_id=request.args.get("asset_id", ""),
            ),
            "governance": artifact_governance_summary(ws_id),
        })

    @app.route("/api/workspaces/<ws_id>/artifacts", methods=["POST"])
    def api_workspace_artifact_create(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        data = request.get_json(silent=True) or {}
        rec = save_artifact(
            workspace_id=ws_id, content=data.get("content", ""),
            artifact_type=data.get("artifact_type", ""),
            title=data.get("title", ""), scope=data.get("scope", "workspace"),
            sensitivity=data.get("sensitivity", ""),
            run_id=data.get("run_id", ""),
            metadata=data.get("metadata", None),
            tags=data.get("tags", None),
            source=data.get("source", "api"),
        )
        if not rec:
            return jsonify({"ok": False, "error": "artifact creation blocked"}), 400
        return jsonify({"ok": True, "artifact": sanitize_record(rec)})

    @app.route("/api/workspaces/<ws_id>/artifacts/upload", methods=["POST"])
    def api_workspace_artifact_upload(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err

        max_size = _get_max_size()

        if request.content_length and request.content_length > max_size + 1_048_576:
            return jsonify({"ok": False, "error": "file_too_large"}), 413

        if "file" not in request.files:
            return jsonify({"ok": False, "error": "no file provided"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"ok": False, "error": "empty filename"}), 400

        artifact_type = request.form.get("artifact_type", "")
        title = request.form.get("title", f.filename)
        scope = request.form.get("scope", "workspace")
        sensitivity = request.form.get("sensitivity", "")
        run_id = request.form.get("run_id", "")
        session_id = request.form.get("session_id", "")

        file_kind, binary = _guess_upload_kind(f.filename, artifact_type)

        logical_type = "user_upload"
        if artifact_type in ("knowledge", "knowledge_source"):
            logical_type = "knowledge_source"
        elif artifact_type in ("chat_attachment",):
            logical_type = "chat_attachment"

        try:
            from storage.file_store import import_user_upload, read_file_content
            file_record = import_user_upload(
                workspace_id=ws_id,
                file_source=f.stream,
                original_name=f.filename,
                logical_type=logical_type,
                file_kind=file_kind,
                binary=binary,
                source="artifact_upload",
                session_id=session_id,
                run_id=run_id,
                sensitivity=sensitivity or "internal",
                metadata={
                    "artifact_type": artifact_type,
                    "title": title,
                    "scope": scope,
                },
            )
        except ValueError as exc:
            msg = str(exc)
            if "file_too_large" in msg:
                return jsonify({"ok": False, "error": "file_too_large", "detail": msg}), 413
            if "unsupported_file_kind" in msg:
                return jsonify({"ok": False, "error": "unsupported_file_kind", "detail": msg}), 400
            return jsonify({"ok": False, "error": "upload_failed", "detail": msg[:200]}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": "upload_failed", "detail": str(exc)[:200]}), 400

        artifact = None
        warnings = []

        if not binary:
            try:
                content = read_file_content(ws_id, file_record.file_id)
                rec = save_artifact(
                    workspace_id=ws_id,
                    content=content,
                    artifact_type=artifact_type or logical_type,
                    title=title,
                    scope=scope,
                    sensitivity=sensitivity,
                    run_id=run_id,
                    metadata={
                        "source_file_id": file_record.file_id,
                        "storage_managed": True,
                    },
                    file_id=file_record.file_id,
                )
                if rec:
                    artifact = sanitize_record(rec)
                else:
                    warnings.append("artifact_creation_blocked")
            except Exception as exc:
                warnings.append(f"artifact_creation_failed: {str(exc)[:120]}")
        else:
            warnings.append("binary_upload_preserved_as_file_only")

        return jsonify({
            "ok": True,
            "file": file_record.as_dict(),
            "artifact": artifact,
            "warnings": warnings,
        })

    @app.route("/api/workspaces/<ws_id>/artifacts/batch-delete", methods=["POST"])
    def api_artifact_batch_delete(ws_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        data = request.get_json(silent=True) or {}
        if not _confirmed(data):
            return _confirm_required()
        ids = data.get("artifact_ids", [])
        if not ids or not isinstance(ids, list):
            return jsonify({"ok": False, "error": "artifact_ids (list) required"}), 400
        deleted = []
        for aid in ids:
            ok = delete_artifact(ws_id, aid, hard=True)
            if ok:
                deleted.append(aid)
        return jsonify({"ok": True, "deleted": len(deleted), "total": len(ids)})

    @app.route("/api/workspaces/<ws_id>/artifacts/<artifact_id>")
    def api_workspace_artifact(ws_id, artifact_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        rec = get_artifact(ws_id, artifact_id)
        if not rec:
            return jsonify({"ok": False, "error": "artifact not found"}), 404
        artifact = sanitize_record(rec, include_metadata=True)
        artifact["governance"] = get_artifact_governance(ws_id, artifact_id)
        return jsonify({"ok": True, "artifact": artifact})

    @app.route("/api/workspaces/<ws_id>/artifacts/<artifact_id>/content")
    def api_artifact_content(ws_id, artifact_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        art = get_artifact(ws_id, artifact_id)
        if not art:
            return jsonify({"ok": False, "error": "artifact not found"}), 404
        # Public content reads remain conservative. Sensitive artifacts should
        # be accessed through server-side tools that can apply purpose-specific
        # gates. Reports are previewable by the UI.
        allow_sensitive = art.artifact_type in {
            "report",
        }
        content = read_artifact_content(ws_id, artifact_id, allow_sensitive=allow_sensitive)
        if content is None:
            return jsonify({"ok": False, "error": "content not accessible"}), 403
        title = art.title if art else ""
        return jsonify({"ok": True, "content": content, "title": title})

    @app.route("/api/workspaces/<ws_id>/artifacts/<artifact_id>", methods=["DELETE"])
    def api_artifact_delete(ws_id, artifact_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        if not _confirmed():
            return _confirm_required()
        ok = delete_artifact(ws_id, artifact_id, hard=True)
        return jsonify({"ok": ok})

    @app.route("/api/workspaces/<ws_id>/artifacts/<artifact_id>/promote", methods=["POST"])
    def api_artifact_promote(ws_id, artifact_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        data = request.get_json(silent=True) or {}
        target = data.get("target_scope", "workspace")
        rec = promote_artifact(ws_id, artifact_id, target)
        if not rec:
            return jsonify({"ok": False, "error": "promotion blocked"}), 400
        return jsonify({"ok": True, "artifact": sanitize_record(rec)})

    @app.route("/api/workspaces/<ws_id>/artifacts/<artifact_id>/summarize")
    def api_artifact_summarize(ws_id, artifact_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        s = summarize_artifact_content(ws_id, artifact_id)
        return jsonify({"ok": True, "summary": s})

    @app.route("/api/workspaces/<ws_id>/runs/<run_id>/artifacts")
    def api_run_artifacts(ws_id, run_id):
        ws_id, err = _validated_ws_id(ws_id)
        if err:
            return err
        return jsonify({"ok": True, **get_run_artifacts(ws_id, run_id)})
