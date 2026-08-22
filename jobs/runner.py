# jobs/runner.py
"""Job runner — executes generic base jobs."""

import time, sys, os, traceback

from jobs.schemas import JobRecord, JobEvent
from jobs.store import get_job, update_job, append_event, append_log, claim_job_for_execution
from jobs.manager import mark_succeeded, mark_failed, mark_cancelled, update_progress
from storage.redaction import redact_text


def run_job(ws_id: str, job_id: str):
    """Execute a job. Entry point called by worker/API."""
    rec = claim_job_for_execution(ws_id, job_id)
    if not rec:
        return

    try:
        append_log(ws_id, job_id, f"Starting {rec.job_type} job")

        if rec.job_type in {"agent_run", "generic_agent_task"}:
            _run_agent_job(rec)
        elif rec.job_type == "export_report":
            _run_export_report(rec)
        elif rec.job_type == "knowledge_index":
            _run_knowledge_index(rec)
        elif rec.job_type == "workflow_run":
            _run_workflow(rec)
        elif rec.job_type == "network_inspection":
            _run_network_inspection(rec)

        # Fresh-get final job for accurate summary
        final = get_job(ws_id, job_id)
        if not final or final.status in {"failed", "cancelled"}:
            return
        mark_succeeded(ws_id, job_id, {
            **dict(final.result_summary or {}),
            "run_count": len(final.run_ids) if final else 0,
            "artifact_count": len(final.output_artifacts) if final else 0,
        })
    except Exception as e:
        error_msg = redact_text(str(e))[:300] or "job_execution_failed"
        mark_failed(ws_id, job_id, error_msg)
        append_log(ws_id, job_id, f"Job failed: {error_msg}", level="error")


def _run_agent_job(rec: JobRecord):
    ws = rec.workspace_id
    jid = rec.job_id
    payload = dict(rec.payload)

    # Check cancel
    if _cancel_check(rec):
        return

    from agent.app.service import get_default_agent_app
    app = get_default_agent_app()
    result = app.submit_user_message(
        user_input=payload.pop("message", ""),
        session_id=None,
        workspace_id=ws,
        metadata={"intent": payload.pop("intent", ""), "job_id": jid},
    )
    result_dict = result.to_dict()
    result_ok = bool(result_dict.get("ok")) and not bool(result_dict.get("errors"))
    # Update job with run info
    update_job(ws, jid, {
        "run_ids": [result_dict.get("run_id", "")],
        "trace_ids": [result_dict.get("trace_id", "")],
        "output_artifacts": result_dict.get("output_artifacts", []),
        "report_artifacts": result_dict.get("report_artifacts", []),
        "result_summary": {
            "ok": result_ok,
            "execution_outcome": str((result_dict.get("metadata") or {}).get("execution_outcome") or ("complete" if result_ok else "failed")),
            "tool_execution_outcome": str((result_dict.get("metadata") or {}).get("tool_execution_outcome") or ("complete" if result_ok else "failed")),
        },
    })
    append_event(ws, jid, JobEvent(
        job_id=jid,
        workspace_id=ws,
        event_type="job_run_finished" if result_ok else "job_run_failed",
        run_id=result_dict.get("run_id", ""),
        message="Agent run completed" if result_ok else "Agent run failed",
    ))
    if not result_ok:
        raw_error = str((result_dict.get("errors") or ["agent_run_failed"])[0])
        mark_failed(ws, jid, redact_text(raw_error)[:300] or "agent_run_failed")

def _run_export_report(rec: JobRecord):
    ws = rec.workspace_id
    jid = rec.job_id

    if _cancel_check(rec):
        return

    payload = rec.payload
    try:
        from core.reports.schemas import ReportRequest
        from core.reports.service import create_report
        result = create_report(
            ReportRequest(
                workspace_id=ws,
                run_id=payload.get("run_id", ""),
                report_type=payload.get("report_type", "generic"),
                title=payload.get("title", "Report"),
                format=payload.get("report_format", "markdown"),
                content=payload.get("content", ""),
            ),
            payload.get("agent_result", {}),
        )
        if result.ok:
            update_job(ws, jid, {
                "report_artifacts": [result.artifact_id],
                "result_summary": {"report_id": result.report_id, "format": result.format},
            })
            append_event(ws, jid, JobEvent(job_id=jid, workspace_id=ws,
                         event_type="job_report_created", artifact_id=result.artifact_id))
        else:
            mark_failed(ws, jid, result.error)
    except Exception as e:
        mark_failed(ws, jid, redact_text(str(e))[:300] or "report_generation_failed")


def _run_knowledge_index(rec: JobRecord):
    ws = rec.workspace_id
    jid = rec.job_id
    if _cancel_check(rec):
        return
    payload = dict(rec.payload or {})
    source_id = str(payload.get("source_id") or "").strip()
    file_id = str(payload.get("file_id") or "").strip()
    from agent.modules.knowledge.service import import_file, reindex_source

    update_progress(ws, jid, current=0, total=1, message="正在构建知识索引")
    if source_id:
        result = reindex_source(ws, source_id)
    elif file_id:
        raw_tags = payload.get("tags") or []
        if not isinstance(raw_tags, list):
            raise ValueError("knowledge_index tags must be a list")
        result = import_file(
            workspace_id=ws,
            source="",
            file_id=file_id,
            title=str(payload.get("title") or ""),
            source_type=str(payload.get("source_type") or "project_doc"),
            scope=str(payload.get("scope") or "workspace"),
            language=str(payload.get("language") or "zh"),
            tags=[str(tag) for tag in raw_tags if str(tag).strip()],
        )
    else:
        raise ValueError("knowledge_index requires source_id or file_id")
    if not result.get("ok"):
        errors = result.get("errors") or [result.get("error") or "knowledge_index_failed"]
        raise RuntimeError(str(errors[0]))
    update_progress(ws, jid, current=1, total=1, message="知识索引已更新")
    update_job(ws, jid, {"result_summary": {
        "status": "completed",
        "job_type": "knowledge_index",
        "source_id": result.get("source_id") or source_id,
        "chunk_count": int(result.get("chunk_count") or 0),
    }})


def _run_network_inspection(rec: JobRecord):
    from extensions.network_operations.service import execute_queued_inspection
    task_id = str((rec.payload or {}).get("task_id") or "").strip()
    if not task_id:
        raise ValueError("network_inspection task_id is required")
    if _cancel_check(rec):
        return
    update_progress(rec.workspace_id, rec.job_id, current=0, total=1, message="正在执行网络巡检")
    task = execute_queued_inspection(rec.workspace_id, task_id, rec.job_id)
    update_job(rec.workspace_id, rec.job_id, {
        "output_artifacts": [task["artifact_id"]] if task.get("artifact_id") else [],
        "result_summary": {"task_id": task_id, "inspection_status": task.get("status"), "succeeded": task.get("succeeded", 0), "failed": task.get("failed", 0)},
    })
    update_progress(rec.workspace_id, rec.job_id, current=1, total=1, message="网络巡检已完成")
    if task.get("status") == "cancelled":
        mark_cancelled(rec.workspace_id, rec.job_id, "Network inspection cancelled")


def _run_workflow(rec: JobRecord):
    from workflows.service import execute_workflow
    payload = dict(rec.payload or {})
    result = execute_workflow(
        rec.workspace_id,
        str(payload.get("workflow_id") or ""),
        payload.get("inputs") or {},
        approvals=payload.get("approvals") or {},
        job_id=rec.job_id,
    )
    update_job(rec.workspace_id, rec.job_id, {"result_summary": {
        "workflow_id": result["workflow_id"],
        "workflow_run_id": result["run_id"],
        "status": result["status"],
    }})
    if result["status"] == "cancelled":
        from jobs.manager import mark_cancelled
        mark_cancelled(rec.workspace_id, rec.job_id, "Workflow run cancelled")
    elif result["status"] != "succeeded":
        raise RuntimeError("workflow_run_failed")


def _cancel_check(rec: JobRecord) -> bool:
    """Check if cancellation was requested. Returns True if should stop."""
    from jobs.store import get_job
    freshest = get_job(rec.workspace_id, rec.job_id)
    if freshest and freshest.cancel_requested:
        if freshest.status == "running":
            mark_cancelled(rec.workspace_id, rec.job_id)
        return True
    return False
