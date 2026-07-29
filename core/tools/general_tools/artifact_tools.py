from __future__ import annotations

from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id

from core.tools.general_tools.shared import _caller_workspace, _contract, _error, _error_inv, _ok, _result, _safe_preview, _unavailable, _workspace_path
"""Split general tool handlers."""


def handle_artifact_search(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    query = (args.get("query") or "").strip().lower()
    evidence_view = str(args.get("evidence_view") or "").strip()
    if evidence_view not in {"", "current", "history", "deliverables"}:
        return _error_inv(inv, "evidence_view must be current, history, or deliverables")
    try:
        validate_workspace_id(ws)
        from artifacts.store import artifact_governance_summary, list_artifacts
        arts = list_artifacts(
            ws, limit=100, evidence_view=evidence_view,
            producer_id=str(args.get("producer_id") or ""),
            asset_id=str(args.get("asset_id") or ""),
        )
        results = []
        for a in arts:
            title = (a.get("title") or "").lower()
            a_type = (a.get("artifact_type") or "").lower()
            if query in title or query in a_type or not query:
                results.append({
                    "artifact_id": a.get("artifact_id", ""),
                    "title": a.get("title", ""),
                    "artifact_type": a.get("artifact_type", ""),
                    "lifecycle": a.get("lifecycle", "active"),
                    "sensitivity": a.get("sensitivity", "internal"),
                    "created_at": a.get("created_at", ""),
                    "producer_id": (a.get("metadata") or {}).get("producer_id", ""),
                    "asset_id": (a.get("metadata") or {}).get("asset_id", ""),
                    "governance": a.get("governance") or {},
                })
        return _ok(inv, "", {
            "results": results[:20], "count": len(results),
            "governance": artifact_governance_summary(ws),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_artifact_read_content_safe(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    art_id = args.get("artifact_id", "")
    try:
        validate_workspace_id(ws)
        from artifacts.store import read_artifact_content, get_artifact
        art = get_artifact(ws, art_id)
        if not art:
            return _error_inv(inv, "artifact not found")
        sensitivity = getattr(art, "sensitivity", "internal")
        art_type = getattr(art, "artifact_type", "")
        if sensitivity == "secret":
            return _ok(inv, "", {
                "preview": "[artifact content not shown]",
                "title": getattr(art, "title", ""),
                "artifact_type": art_type,
                "sensitivity": sensitivity,
            })
        allow = sensitivity != "confidential"
        content = read_artifact_content(ws, art_id, allow_sensitive=allow)
        if content is None:
            return _error_inv(inv, "content not accessible")
        if art_type in ("input_data", "output_data", "report"):
            # Complete data/report artifacts can be analyzed directly by the LLM.
            preview_len = len(str(content))
        elif sensitivity == "confidential":
            preview_len = 200
        else:
            preview_len = 2000
        return _ok(inv, "", {
            "artifact_id": art_id,
            "preview": _safe_preview(str(content), preview_len),
            "content_chars": len(str(content)),
            "content_complete": True,
            "title": getattr(art, "title", ""),
            "artifact_type": art_type,
            "sensitivity": sensitivity,
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_artifact_save_result(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    title = args.get("title", "tool_result")
    content = str(args.get("content", ""))
    a_type = args.get("artifact_type", "knowledge_doc")
    try:
        validate_workspace_id(ws)
        from artifacts.store import save_artifact
        rec = save_artifact(workspace_id=ws, content=content, title=title,
                            artifact_type=a_type, sensitivity="internal")
        if not rec:
            return _error_inv(inv, "artifact save blocked or failed")
        return _ok(inv, "", {
            "artifact_id": rec.artifact_id,
            "artifact_ids": [rec.artifact_id],
            "title": title,
            "artifact_type": a_type,
            "file_id": getattr(rec, "file_id", ""),
        })
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_artifact_tag(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    art_id = args.get("artifact_id", "")
    tags = args.get("tags", [])
    try:
        validate_workspace_id(ws)
        from artifacts.store import get_artifact, update_artifact_tags
        art = get_artifact(ws, art_id)
        if not art:
            return _error_inv(inv, "artifact not found")
        existing = list(getattr(art, "tags", []) or [])
        for t in tags:
            if t not in existing:
                existing.append(t)
        if not update_artifact_tags(ws, art_id, existing):
            return _error_inv(inv, "artifact tag update failed")
        return _ok(inv, "", {"artifact_id": art_id, "tags": existing})
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


def handle_artifact_delete_soft(inv: ToolInvocation) -> dict:
    args = inv.arguments
    ws = _caller_workspace(inv)
    art_id = args.get("artifact_id", "")
    try:
        validate_workspace_id(ws)
        from artifacts.store import delete_artifact
        ok = delete_artifact(ws, art_id, hard=True)
        return _ok(inv, f"Artifact {art_id} deleted={ok}.", {"deleted": ok}) if ok else _error_inv(inv, "delete failed")
    except Exception as e:
        return _error_inv(inv, str(e)[:200])


__all__ = ['handle_artifact_search', 'handle_artifact_read_content_safe', 'handle_artifact_save_result', 'handle_artifact_tag', 'handle_artifact_delete_soft']
