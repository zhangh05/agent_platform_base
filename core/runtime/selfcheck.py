# runtime/selfcheck.py
"""Runtime selfcheck — consistency validation across workspace, run, job, artifact, trace.

Only reads and validates structure. Never modifies data.
Never reads full sensitive content. Never exposes absolute paths.
"""

import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from storage.paths import workspace_root

ROOT = Path(__file__).resolve().parents[2]
_STALE_RUNNING_SECONDS = 30 * 60


class SelfcheckStatus:
    HEALTHY = "healthy"
    WARNING = "warning"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass
class SelfcheckIssue:
    severity: str = "info"  # info | warning | error | critical
    code: str = ""
    message: str = ""
    ref_id: str = ""
    suggested_action: str = ""

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "ref_id": self.ref_id,
            "suggested_action": self.suggested_action,
        }


@dataclass
class SelfcheckResult:
    status: str = SelfcheckStatus.HEALTHY
    issues: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "issues": [i.as_dict() for i in self.issues],
            "checks": self.checks,
        }


def _safe_read_json(path: Path) -> Optional[dict]:
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        return record if isinstance(record, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _safe_list_dir(path: Path) -> list:
    try:
        return [p for p in path.iterdir() if not p.name.startswith(".")]
    except Exception:
        return []


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def run_selfcheck(workspace_id: str = "default") -> SelfcheckResult:
    """Run full selfcheck for a workspace. Returns structured result."""
    result = SelfcheckResult()
    run_checks(result, workspace_id)
    # Determine overall status
    severities = {i.severity for i in result.issues}
    if "critical" in severities:
        result.status = SelfcheckStatus.FAILED
    elif "error" in severities:
        result.status = SelfcheckStatus.DEGRADED
    elif "warning" in severities:
        result.status = SelfcheckStatus.WARNING
    return result


def run_checks(result: SelfcheckResult, ws_id: str):
    """Execute all selfcheck checks. Appends issues to result."""
    ws_dir = workspace_root(ws_id)

    # 1. Workspace root exists
    if not ws_dir.exists():
        result.issues.append(SelfcheckIssue("error", "WORKSPACE_ROOT_MISSING",
            f"Workspace '{ws_id}' not found", ws_id, "Create workspace first"))
        result.checks["workspace_root"] = "missing"
        return  # Cannot proceed without workspace
    result.checks["workspace_root"] = "ok"

    # 2. State file
    state_file = ws_dir / "sys" / "state.json"
    state = _safe_read_json(state_file)
    result.checks["state_json"] = "ok" if state else "missing"
    if not state:
        result.issues.append(SelfcheckIssue(
            "warning", "STATE_JSON_MISSING", "Workspace state is missing or invalid",
            ws_id, "Repair workspace state",
        ))

    # 3. Runs directory
    runs_dir = ws_dir / "runs"
    from storage.run_record_store import is_run_record_file

    all_run_files = _safe_list_dir(runs_dir) if runs_dir.is_dir() else []
    run_files = sorted(
        (path for path in all_run_files if path.is_file() and is_run_record_file(path)),
        key=_safe_mtime,
        reverse=True,
    )
    trace_files = sorted(
        (path for path in all_run_files if path.is_file() and path.name.endswith(".trace.json")),
        key=_safe_mtime,
        reverse=True,
    )
    result.checks["runs_count"] = len(run_files)
    for rf in run_files[:20]:  # Check first 20
        record = _safe_read_json(rf)
        if record is None:
            result.issues.append(SelfcheckIssue("warning", "RUN_JSON_INVALID",
                f"Run record not parseable: {rf.name}", rf.name[:12],
                "Remove or repair the run record"))
    result.checks["run_traces_count"] = len(trace_files)
    for tf in trace_files[:20]:
        if _safe_read_json(tf) is None:
            result.issues.append(SelfcheckIssue("warning", "TRACE_JSON_INVALID",
                f"Run trace not parseable: {tf.name}", tf.name[:12],
                "Remove or repair the run trace"))

    # 4. Artifact metadata and FileRecord linkage
    art_index = ws_dir / "index" / "artifacts.jsonl"
    file_index = ws_dir / "index" / "files.jsonl"
    artifact_records, invalid_artifact_lines = _read_jsonl_records(art_index)
    file_records, invalid_file_lines = _read_jsonl_records(file_index)
    latest_artifacts = {
        str(record.get("artifact_id") or ""): record
        for record in artifact_records if record.get("artifact_id")
    }
    latest_files = {
        str(record.get("file_id") or ""): record
        for record in file_records if record.get("file_id")
    }
    active_artifacts = [
        record for record in latest_artifacts.values()
        if record.get("lifecycle", "active") != "deleted"
    ]
    result.checks["artifacts_count"] = len(active_artifacts)
    if invalid_artifact_lines or invalid_file_lines:
        result.issues.append(SelfcheckIssue(
            "warning", "STORAGE_INDEX_INVALID",
            f"Malformed storage index lines: artifacts={invalid_artifact_lines}, files={invalid_file_lines}",
            ws_id, "Repair or rebuild the affected index",
        ))
    for artifact in active_artifacts[:100]:
        artifact_id = str(artifact.get("artifact_id") or "")
        file_id = str(artifact.get("file_id") or "")
        if not file_id or file_id not in latest_files:
            result.issues.append(SelfcheckIssue(
                "warning", "ARTIFACT_FILE_REF_MISSING",
                f"Artifact {artifact_id} has no valid FileRecord",
                artifact_id[:12], "Repair artifact-to-file linkage",
            ))

    # 5. Jobs directory
    jobs_dir = ws_dir / "jobs"
    job_files = list(jobs_dir.glob("*/*.json")) if jobs_dir.is_dir() else []
    job_files = [path for path in job_files if path.name == f"{path.parent.name}.json"]
    result.checks["jobs_count"] = len(job_files)
    for path in job_files[:100]:
        job = _safe_read_json(path)
        if job is None:
            result.issues.append(SelfcheckIssue(
                "warning", "JOB_JSON_INVALID", f"Job record not parseable: {path.name}",
                path.parent.name[:12], "Repair or remove the job record",
            ))
            continue
        if job.get("status") == "running" and _age_seconds(job.get("updated_at")) > _STALE_RUNNING_SECONDS:
            job_id = str(job.get("job_id") or path.parent.name)
            result.issues.append(SelfcheckIssue(
                "error", "JOB_STALE_RUNNING", f"Job {job_id} is stuck in running state",
                job_id[:12], "Reconcile interrupted jobs",
            ))

    # 6. Cross-reference: artifact refs in recent runs
    for rf in run_files[:10]:
        record = _safe_read_json(rf)
        if not record:
            continue
        art_refs = record.get("artifact_refs", []) or []
        for ref in art_refs:
            art_id = ref.get("artifact_id", "") if isinstance(ref, dict) else str(ref)
            if art_id not in latest_artifacts:
                result.issues.append(SelfcheckIssue("warning", "ARTIFACT_REF_MISSING",
                    f"Run {rf.stem[:12]} references missing artifact {art_id}",
                    art_id[:12], "The artifact may have been removed"))

    # 7. Cross-reference: job refs in runs
    for rf in run_files[:10]:
        record = _safe_read_json(rf)
        if not record:
            continue
        job_refs = record.get("job_refs", []) or []
        for jr in job_refs:
            job_id = jr if isinstance(jr, str) else jr.get("job_id", "")
            job_path = jobs_dir / job_id / f"{job_id}.json"
            if not job_path.is_file():
                result.issues.append(SelfcheckIssue("info", "JOB_REF_MISSING",
                    f"Run {rf.stem[:12]} references job {job_id} but not found",
                    job_id[:12], "Job may be in different workspace"))

    # 8. Path safety: no absolute paths in run records
    for rf in run_files[:10]:
        record = _safe_read_json(rf)
        if not record:
            continue
        if _contains_absolute_path(record):
            result.issues.append(SelfcheckIssue("warning", "ABSOLUTE_PATH",
                f"Run record {rf.stem} contains absolute path", rf.stem[:12],
                "Redact absolute paths"))

    # 9. quality_summary structure
    for rf in run_files[:10]:
        record = _safe_read_json(rf)
        if not record:
            continue
        qs = record.get("quality_summary", {})
        if isinstance(qs, dict) and qs:
            for field in ("source_residue_count", "silent_drop_count"):
                if field in qs and not isinstance(qs[field], int):
                    result.issues.append(SelfcheckIssue("warning", "QUALITY_SUMMARY_TYPE",
                        f"quality_summary.{field} is not int in {rf.stem[:12]}",
                        rf.stem[:12], "Fix quality_summary format"))

    # 10. Verify canonical capabilities
    try:
        from agent.capabilities.catalog import list_enabled
        enabled = sorted(item["capability_id"] for item in list_enabled())
        if not enabled:
            result.issues.append(SelfcheckIssue("warning", "CAPABILITY_COUNT",
                "No enabled capabilities found", "", "Enable a catalog capability"))
        result.checks["enabled_capabilities"] = enabled
    except Exception:
        result.issues.append(SelfcheckIssue("warning", "CAPABILITY_CATALOG_UNAVAILABLE",
            "Capability catalog failed", "", "Check agent.capabilities.catalog"))

    # 11. Removed business APIs not restored
    try:
        backend_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "backend").rglob("*.py")
        )
        removed_path = "/api/" + "legacy-business"
        if removed_path in backend_source and "route(" in backend_source:
            result.issues.append(SelfcheckIssue("critical", "FORBIDDEN_API",
                "removed business API found in backend", "",
                "Immediately remove the retired business route"))
        result.checks["forbidden_api"] = "ok"
    except Exception:
        result.checks["forbidden_api"] = "unavailable"

    # 12. No absolute path leak in trace metadata
    for tf in trace_files[:20]:
        record = _safe_read_json(tf)
        if record and _contains_absolute_path(record):
            result.issues.append(SelfcheckIssue("warning", "TRACE_PATH_LEAK",
                f"Trace record {tf.stem} contains absolute path", tf.stem[:12],
                "Redact absolute paths from trace metadata"))

    # 13. Durable events must belong to a real task id.
    event_dir = ws_dir / "durable" / "events"
    invalid_events = 0
    empty_task_events = 0
    if event_dir.is_dir():
        for path in event_dir.glob("*.events.json"):
            rows, malformed = _read_jsonl_records(path)
            invalid_events += malformed
            empty_task_events += sum(1 for row in rows if not str(row.get("task_id") or "").strip())
    result.checks["durable_event_invalid"] = invalid_events
    result.checks["durable_event_empty_task_id"] = empty_task_events
    if invalid_events:
        result.issues.append(SelfcheckIssue(
            "warning", "DURABLE_EVENT_INVALID", f"Malformed durable events: {invalid_events}",
            ws_id, "Repair the durable event stream",
        ))
    if empty_task_events:
        result.issues.append(SelfcheckIssue(
            "error", "DURABLE_EVENT_TASK_MISSING",
            f"Durable events without task_id: {empty_task_events}", ws_id,
            "Remove invalid events and fix event producers",
        ))

    # 14. Tool Runtime current policy/governance summary.
    # All canonical tools are active by default.
    # V02_FORBIDDEN_TOOLS is a separate policy blacklist
    # (tool names that should never be invoked) — it
    # is NOT a subset of the canonical registry.
    try:
        from core.tools.canonical_registry import CANONICAL_REGISTRY
        from core.tools.policy import V02_FORBIDDEN_TOOLS

        result.checks["tool_runtime"] = "ok"
        result.checks["tool_registered_count"] = len(CANONICAL_REGISTRY)
        result.checks["tool_forbidden_count"] = len(V02_FORBIDDEN_TOOLS)
        result.checks["tool_forbidden_list"] = sorted(V02_FORBIDDEN_TOOLS)
        result.checks["tool_governance"] = {
            "active": len(CANONICAL_REGISTRY),
            "disabled": 0,
            "internal": 0,
            "forbidden": 0,
        }
    except Exception:
        result.checks["tool_runtime"] = "unavailable"


def _read_jsonl_records(path: Path) -> tuple[list[dict], int]:
    if not path.is_file():
        return [], 0
    rows: list[dict] = []
    malformed = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 1
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(record, dict):
            rows.append(record)
        else:
            malformed += 1
    return rows, malformed


def _age_seconds(value) -> float:
    if not value:
        return float("inf")
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return float("inf")


def _contains_absolute_path(record: dict) -> bool:
    text = json.dumps(record, ensure_ascii=False)
    return "/Users/" in text or "/home/" in text or bool(re.search(r"[A-Za-z]:\\\\", text))
