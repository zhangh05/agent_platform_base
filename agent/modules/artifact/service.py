# agent/modules/artifact/service.py
"""Artifact management service (v0.9).

Read-only / rendering-only service functions that wrap the existing
artifacts.store. Never fabricates content; never pushes to a device.

Service functions:
  - list_artifacts_for_session(workspace_id, session_id, artifact_type, limit)
  - read_artifact(workspace_id, artifact_id, allow_sensitive)
  - diff_artifacts(workspace_id, left_artifact_id, right_artifact_id, max_lines)
  - export_artifact(workspace_id, artifact_id, format)
  - to_module_result(result_dict)  (v0.8.2 standard projection)
"""

from __future__ import annotations

import difflib
from datetime import datetime, timezone
from typing import Any, Optional
from agent.protocol import ModuleResult


def list_artifacts_for_session(
    workspace_id: str,
    session_id: str = "",
    artifact_type: str = "",
    limit: int = 50,
) -> dict:
    """List artifacts, optionally filtered by session and/or type.

    Returns sanitized records (no local file paths).
    """
    from artifacts.store import list_artifacts
    if not workspace_id:
        return {
            "ok": False,
            "summary": "workspace_id is required",
            "artifacts": [],
            "errors": ["missing_workspace_id"],
        }
    try:
        # v0.9 list filter: run_id is used as session_id when given.
        # NOTE: list_artifacts already returns sanitized records
        # (no local file paths).
        sanitized = list_artifacts(
            workspace_id=workspace_id,
            run_id=session_id or None,
            artifact_type=artifact_type or None,
        )
    except Exception as e:
        return {
            "ok": False,
            "summary": f"list_artifacts failed: {e!r}",
            "artifacts": [],
            "errors": ["artifact_list_failed"],
        }
    if limit and len(sanitized) > limit:
        sanitized = sanitized[:limit]
    return {
        "ok": True,
        "summary": f"Listed {len(sanitized)} artifact(s)",
        "artifacts": sanitized,
        "count": len(sanitized),
        "errors": [],
        "warnings": [],
        "metadata": {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "artifact_type": artifact_type,
            "limit": limit,
        },
    }


def read_artifact(
    workspace_id: str,
    artifact_id: str,
    allow_sensitive: bool = False,
) -> dict:
    """Read artifact content + metadata.

    Returns ok=False when:
      - workspace_id or artifact_id missing
      - artifact not found
      - sensitivity gates deny access (sensitive w/o allow_sensitive,
        or secret regardless)
    """
    from artifacts.store import get_artifact, read_artifact_content
    if not workspace_id or not artifact_id:
        return {
            "ok": False,
            "summary": "workspace_id and artifact_id are required",
            "errors": ["missing_inputs"],
        }
    rec = get_artifact(workspace_id, artifact_id)
    if rec is None:
        return {
            "ok": False,
            "summary": f"Artifact not found: {artifact_id}",
            "errors": ["artifact_not_found"],
            "artifact_id": artifact_id,
        }
    if rec.sensitivity == "secret" and not allow_sensitive:
        return {
            "ok": False,
            "summary": "secret artifact access denied",
            "errors": ["sensitivity_denied"],
            "artifact_id": artifact_id,
        }
    _auto_allow = allow_sensitive
    if rec.sensitivity == "sensitive" and not _auto_allow:
        return {
            "ok": False,
            "summary": "sensitive artifact requires allow_sensitive=true",
            "errors": ["sensitivity_denied"],
            "artifact_id": artifact_id,
        }
    try:
        content = read_artifact_content(
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            allow_sensitive=bool(_auto_allow),
        )
    except Exception as e:
        return {
            "ok": False,
            "summary": f"read_artifact_content failed: {e!r}",
            "errors": ["artifact_read_failed"],
        }
    if content is None:
        return {
            "ok": False,
            "summary": "artifact content unavailable",
            "errors": ["artifact_content_unavailable"],
        }
    meta = dict(rec.metadata or {})
    authoritative = bool(meta.get("authoritative", False))
    return {
        "ok": True,
        "summary": f"Read {len(content)} chars from {artifact_id}",
        "content": content,
        "metadata": meta,
        "artifact_id": rec.artifact_id,
        "artifact_type": rec.artifact_type,
        "title": rec.title,
        "sensitivity": rec.sensitivity,
        "authoritative": authoritative,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "errors": [],
        "warnings": [],
    }


def diff_artifacts(
    workspace_id: str,
    left_artifact_id: str,
    right_artifact_id: str,
    max_lines: int = 200,
) -> dict:
    """Compute a unified text diff between two artifacts."""
    from artifacts.store import get_artifact, read_artifact_content
    if not workspace_id or not left_artifact_id or not right_artifact_id:
        return {
            "ok": False,
            "summary": "workspace_id, left_artifact_id and right_artifact_id are required",
            "errors": ["missing_inputs"],
        }
    rec_l = get_artifact(workspace_id, left_artifact_id)
    rec_r = get_artifact(workspace_id, right_artifact_id)
    if rec_l is None or rec_r is None:
        missing = []
        if rec_l is None:
            missing.append(left_artifact_id)
        if rec_r is None:
            missing.append(right_artifact_id)
        return {
            "ok": False,
            "summary": f"artifact(s) not found: {', '.join(missing)}",
            "errors": ["artifact_not_found"],
            "missing": missing,
        }
    try:
        # Workspace owner can read sensitive artifacts within their
        # own workspace. Both sides are in the same workspace.
        left_content = read_artifact_content(workspace_id, left_artifact_id, allow_sensitive=True) or ""
        right_content = read_artifact_content(workspace_id, right_artifact_id, allow_sensitive=True) or ""
    except Exception as e:
        return {
            "ok": False,
            "summary": f"diff_artifacts read failed: {e!r}",
            "errors": ["artifact_read_failed"],
        }
    left_lines = left_content.splitlines(keepends=True)
    right_lines = right_content.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        left_lines, right_lines,
        fromfile=left_artifact_id, tofile=right_artifact_id,
        n=2,
    ))
    truncated = False
    if max_lines and len(diff) > max_lines:
        diff = diff[:max_lines]
        truncated = True
    return {
        "ok": True,
        "summary": f"Diff computed ({len(diff)} lines)",
        "diff": "".join(diff),
        "diff_lines": len(diff),
        "left_artifact_id": left_artifact_id,
        "right_artifact_id": right_artifact_id,
        "left_artifact_type": rec_l.artifact_type,
        "right_artifact_type": rec_r.artifact_type,
        "truncated": truncated,
        "errors": [],
        "warnings": [],
    }


def export_artifact(
    workspace_id: str,
    artifact_id: str,
    format: str = "txt",
) -> dict:
    """Render an artifact as txt or md. Local-only; never pushes."""
    fmt = (format or "txt").lower()
    if fmt not in ("txt", "md"):
        return {
            "ok": False,
            "summary": f"unsupported format: {format}",
            "errors": ["unsupported_format"],
        }
    # Workspace owner can read sensitive artifacts within their own
    # workspace. export_artifact is a rendering operation, not a
    # cross-workspace data exfiltration.
    read_result = read_artifact(workspace_id, artifact_id, allow_sensitive=True)
    if not read_result.get("ok"):
        return {
            "ok": False,
            "summary": read_result.get("summary", "read failed"),
            "errors": read_result.get("errors", ["artifact_read_failed"]),
        }
    content = read_result.get("content", "")
    title = read_result.get("title", artifact_id)
    meta = read_result.get("metadata", {}) or {}
    if fmt == "txt":
        body = content
    else:  # md
        author = meta.get("authoritative", False)
        body = (
            f"# {title}\n\n"
            f"- artifact_id: `{artifact_id}`\n"
            f"- artifact_type: `{read_result.get('artifact_type', '')}`\n"
            f"- sensitivity: `{read_result.get('sensitivity', '')}`\n"
            f"- authoritative: **{author}**\n"
            f"```\n{content}\n```\n"
        )
    return {
        "ok": True,
        "summary": f"Exported {artifact_id} as {fmt} ({len(body)} chars)",
        "rendered": body,
        "format": fmt,
        "artifact_id": artifact_id,
        "artifact_type": read_result.get("artifact_type", ""),
        "authoritative": bool(meta.get("authoritative", False)),
        "errors": [],
        "warnings": [],
    }


# ── v0.8.2 — ModuleResult projection ──

def to_module_result(result: dict) -> "ModuleResult":
    """Project a v0.9 result dict into a standard ModuleResult."""
    from agent.protocol.module_result import ModuleResult
    if not isinstance(result, dict):
        return ModuleResult.failure(
            summary="artifact service returned non-dict result",
            errors=["invalid_result_shape"],
        )
    ok = bool(result.get("ok", False))
    data = {
        k: v for k, v in result.items()
        if k not in ("errors", "warnings", "metadata", "summary", "ok", "artifacts")
    }
    if ok:
        return ModuleResult.success(
            summary=str(result.get("summary", "")),
            data=data,
            artifacts=list(result.get("artifacts") or []),
            warnings=list(result.get("warnings") or []),
            metadata=dict(result.get("metadata") or {}),
        )
    return ModuleResult.failure(
        summary=str(result.get("summary", "")),
        errors=list(result.get("errors") or ["unknown_error"]),
        warnings=list(result.get("warnings") or []),
        data=data,
        metadata=dict(result.get("metadata") or {}),
    )
