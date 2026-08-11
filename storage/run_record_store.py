"""Run-record repository helpers."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from storage.redaction import redact_text, redact_value
from storage.records import atomic_save_json, workspace_record_dir, workspace_record_file
from storage.ids import validate_run_id


def write_run_record(state: SimpleNamespace, workspace_id: str = "default") -> str:
    ws_id = str(workspace_id or "").strip()
    if not ws_id:
        raise ValueError("workspace_id is required")
    run_id = _safe_run_id(getattr(state, "request_id", "") or f"run_{int(time.time())}")
    created_at = getattr(state, "created_at", "") or _now_iso()
    result = getattr(state, "skill_results", {}) or getattr(state, "tool_results", {}) or {}
    state_warnings = list(getattr(state, "warnings", []) or [])
    context = getattr(state, "context", {}) or {}
    llm_ctx = context.get("llm", {}) if isinstance(context, dict) else {}
    output_lines = len(result.get("output", "").split("\n")) if result.get("output") else 0

    artifact_refs = _safe_ref_list(context.get("artifact_refs", []))
    if not artifact_refs:
        artifact_refs = _safe_artifact_refs_from_context(state)

    record = {
        "run_id": run_id,
        "workspace_id": ws_id,
        "session_id": getattr(state, "session_id", "") or "",
        "request_id": getattr(state, "request_id", ""),
        "created_at": created_at,
        "user_input_summary": redact_text(getattr(state, "user_input", "") or "")[:120],
        "intent": getattr(state, "intent", ""),
        "capability": context.get("capability_id", "") if isinstance(context, dict) else "",
        "runtime_mode": getattr(state, "runtime_mode", ""),
        "started_at": created_at,
        "finished_at": _now_iso(),
        "status": _safe_status(state, result),
        "result_counts": {
            "output_lines": output_lines,
            "warnings": len(state_warnings),
            "artifacts": len(result.get("output_artifacts", [])),
        },
        "final_response_summary": redact_text(getattr(state, "final_response", "") or "")[:300],
        "verification": {},
        "quality_summary": _safe_quality_summary(result),
        "warning_count": len(state_warnings),
        "llm_metadata": {
            "used": llm_ctx.get("used", False),
            "provider": llm_ctx.get("provider", ""),
            "model": llm_ctx.get("model", ""),
            "task": llm_ctx.get("task", ""),
            "config_source": llm_ctx.get("config_source", "default"),
            "fallback_reason": llm_ctx.get("fallback_reason"),
        },
        "memory_written": context.get("memory_written", False) if isinstance(context, dict) else False,
        "workspace_updated": context.get("workspace_updated", False) if isinstance(context, dict) else False,
        "artifact_refs": artifact_refs,
        "report_refs": _safe_ref_list(context.get("report_refs", [])) if isinstance(context, dict) else [],
        "job_refs": _safe_ref_list(context.get("job_refs", [])) if isinstance(context, dict) else [],
        "trace_id": getattr(state, "trace_id", "") or "",
        "artifacts": [],
        "warnings": [redact_text(str(w))[:300] for w in (getattr(state, "warnings", []) or [])[:20]],
        "error": redact_value(getattr(state, "error", None)),
        "sensitivity": "internal",
        "redaction_applied": True,
    }
    atomic_save_json(ws_id, ("runs", f"{run_id}.json"), redact_value(record))
    session_id = str(record.get("session_id") or "")
    if session_id:
        try:
            from storage.session_store import add_run_to_session, auto_title_from_input, ensure_session

            ensure_session(session_id, ws_id, created_at=created_at)
            add_run_to_session(session_id, run_id, ws_id)
            user_input = getattr(state, "user_input", "") or ""
            if user_input:
                auto_title_from_input(session_id, user_input, ws_id)
        except Exception:
            pass
    return run_id


def write_sub_agent_run(
    *,
    ws_id: str,
    subtask_id: str,
    parent_run_id: str,
    child_run_id: str,
    instruction: str,
    ok: bool,
    final_response: str = "",
    tool_calls_count: int = 0,
    steps: int = 0,
    visible_tool_ids: list[str] | None = None,
) -> str:
    """Persist a child-agent run record through the current run repository."""
    run_id = validate_run_id(child_run_id)
    record = {
        "run_id": run_id,
        "workspace_id": ws_id,
        "session_id": str(subtask_id or ""),
        "subtask_id": str(subtask_id or ""),
        "parent_run_id": validate_run_id(parent_run_id),
        "child_run_id": run_id,
        "is_sub_agent": True,
        "created_at": _now_iso(),
        "started_at": _now_iso(),
        "finished_at": _now_iso(),
        "status": "ok" if ok else "error",
        "user_input_summary": redact_text(str(instruction or ""))[:120],
        "final_response_summary": redact_text(str(final_response or ""))[:300],
        "tool_calls_count": max(0, int(tool_calls_count or 0)),
        "steps": max(0, int(steps or 0)),
        "visible_tool_ids": [str(item) for item in (visible_tool_ids or [])[:50]],
        "sensitivity": "internal",
        "redaction_applied": True,
    }
    atomic_save_json(ws_id, ("runs", f"{run_id}.json"), redact_value(record))
    return run_id


def save_trace_record(workspace_id: str, run_id: str, record: dict[str, Any]) -> None:
    rid = validate_run_id(run_id)
    atomic_save_json(workspace_id, ("runs", f"{rid}.trace.json"), redact_value(record))


def update_run_record(workspace_id: str, run_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    record = get_run(run_id, workspace_id)
    if not record:
        return {}
    record.update(redact_value(updates))
    safe_record = redact_value(record)
    atomic_save_json(workspace_id, ("runs", f"{validate_run_id(run_id)}.json"), safe_record)
    return safe_record


def read_run_sidecar(workspace_id: str, run_id: str, suffix: str = ".json") -> dict[str, Any]:
    rid = validate_run_id(run_id)
    safe_suffix = suffix if suffix in {".json", ".trace.json"} else ".json"
    path = workspace_record_file(workspace_id, "runs", f"{rid}{safe_suffix}")
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_run_id(raw: str) -> str:
    if raw:
        try:
            return validate_run_id(raw)
        except ValueError:
            pass
    return f"run_{int(time.time() * 1000)}"


def _safe_quality_summary(result: dict) -> dict:
    qs = result.get("quality_summary", {})
    keys = [
        "source_residue_count",
        "silent_drop_count",
        "unsupported_count",
        "safe_drop_count",
        "review_required_count",
    ]
    if isinstance(qs, dict):
        return {key: qs.get(key, 0) if isinstance(qs.get(key, 0), int) else 0 for key in keys}
    return {
        "source_residue_count": 0,
        "silent_drop_count": 0,
        "review_required_count": len(result.get("manual_review", [])),
        "unsupported_count": len(result.get("unsupported", [])),
        "safe_drop_count": 0,
    }


def _safe_status(state: SimpleNamespace, result: dict) -> str:
    context = getattr(state, "context", {}) or {}
    if getattr(state, "error", None):
        return "error"
    if isinstance(context, dict) and context.get("capability_status") == "planned":
        return "planned"
    result_ok = getattr(state, "result_ok", None)
    if result_ok is False:
        return "error"
    if getattr(state, "result_errors", None):
        return "error"
    return "ok"


def _safe_ref_list(items) -> list:
    safe = []
    if not isinstance(items, list):
        return safe
    for item in items[:50]:
        if isinstance(item, str):
            value = redact_text(item)
            if "/" not in value and "\\" not in value:
                safe.append(value[:120])
        elif isinstance(item, dict):
            safe_item = {}
            for key in ("artifact_id", "report_id", "job_id", "type", "title", "summary"):
                if key in item:
                    safe_item[key] = redact_text(str(item[key]))[:200]
            if safe_item:
                safe.append(safe_item)
    return safe


def _safe_artifact_refs_from_context(state: SimpleNamespace) -> list:
    refs = []
    context = getattr(state, "context", {}) or {}
    if not isinstance(context, dict):
        return refs
    for key in ("input_artifacts", "output_artifacts", "report_artifacts"):
        for art_id in context.get(key, [])[:20]:
            refs.append({"artifact_id": redact_text(str(art_id))[:120], "type": key})
    return refs


def get_run_session_id(workspace_id: str, run_id: str) -> str:
    for suffix in (".json", ".trace.json"):
        data = read_run_sidecar(workspace_id, run_id, suffix)
        session_id = str(data.get("session_id") or "")
        if session_id:
            return session_id
    return ""


def get_run(run_id: str, workspace_id: str = "default") -> dict[str, Any]:
    rid = validate_run_id(run_id)
    return read_run_sidecar(workspace_id, rid, ".json")


def list_runs(workspace_id: str = "default", limit: int = 50, **kwargs) -> list[dict[str, Any]]:
    fetch_limit = limit
    session_id = str(kwargs.get("session_id") or "")
    if session_id:
        fetch_limit = max(limit, 100)
    rows: list[dict[str, Any]] = []
    runs_dir = workspace_record_dir(workspace_id, "runs")
    for path in sorted(runs_dir.glob("*.json"), reverse=True):
        if not _is_run_record_file(path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(data, dict):
            rows.append(data)
        if len(rows) >= fetch_limit:
            break
    if session_id:
        rows = [row for row in rows if row.get("session_id") == session_id]
    return sorted(rows, key=run_sort_key, reverse=True)[:limit]


def get_last_run(workspace_id: str = "default") -> dict[str, Any] | None:
    runs = list_runs(workspace_id, limit=1)
    return runs[0] if runs else None


def run_sort_key(record: dict[str, Any]) -> tuple:
    stamp = record.get("created_at") or record.get("started_at") or record.get("finished_at") or ""
    parsed = _timestamp_seconds(str(stamp))
    if parsed is not None:
        return (1, parsed, str(record.get("run_id", "")))
    return (0, str(stamp), str(record.get("run_id", "")))


def _timestamp_seconds(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _is_run_record_file(name: str) -> bool:
    return name.endswith(".json") and not name.endswith((
        ".trace.json",
        ".decision.json",
        ".artifacts.json",
    ))


def is_run_record_file(path_or_name) -> bool:
    name = getattr(path_or_name, "name", path_or_name)
    return _is_run_record_file(str(name))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
