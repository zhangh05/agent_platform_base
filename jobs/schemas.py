# jobs/schemas.py
"""JobRecord, JobEvent, JobProgress schemas."""

import uuid
from dataclasses import dataclass, field

from agent.runtime.utils import now_iso

JOB_STATUSES = {"created", "queued", "running", "succeeded", "failed", "cancelled", "paused"}
JOB_TYPES = {"agent_run", "export_report", "knowledge_index", "generic_agent_task"}
ENABLED_JOB_TYPES = {"agent_run", "export_report", "knowledge_index", "generic_agent_task"}
EVENT_TYPES = {"job_created", "job_queued", "job_started", "job_progress", "job_step_started",
               "job_step_finished", "job_run_started", "job_run_finished",
               "job_artifact_saved", "job_report_created",
               "job_warning", "job_failed", "job_succeeded",
               "job_cancel_requested", "job_cancelled", "job_retried"}


def _ts(): return now_iso()


@dataclass
class JobProgress:
    current: int = 0
    total: int = 0
    message: str = ""
    current_step: str = ""
    updated_at: str = field(default_factory=_ts)

    @property
    def percent(self) -> int:
        if self.total:
            return min(100, int((self.current / self.total) * 100))
        return 0

    def as_dict(self):
        return {"current": self.current, "total": self.total, "percent": self.percent,
                "message": self.message, "current_step": self.current_step, "updated_at": self.updated_at}


@dataclass
class JobRecord:
    job_id: str = field(default_factory=lambda: f"job_{uuid.uuid4().hex[:8]}")
    workspace_id: str = "default"
    job_type: str = "agent_run"
    title: str = ""
    description: str = ""
    status: str = "created"
    progress: dict = field(default_factory=lambda: JobProgress().as_dict())
    payload: dict = field(default_factory=dict)
    input_artifacts: list = field(default_factory=list)
    output_artifacts: list = field(default_factory=list)
    report_artifacts: list = field(default_factory=list)
    artifact_refs: list = field(default_factory=list)
    run_ids: list = field(default_factory=list)
    trace_ids: list = field(default_factory=list)
    capability_id: str = ""
    skill: str = ""
    module: str = ""
    created_by: str = "user"
    created_at: str = field(default_factory=_ts)
    updated_at: str = field(default_factory=_ts)
    started_at: str = ""
    finished_at: str = ""
    cancel_requested: bool = False
    retry_count: int = 0
    max_retries: int = 3
    error: str = ""
    warnings: list = field(default_factory=list)
    result_summary: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    redaction_applied: bool = False

    def as_dict(self): return {f: getattr(self, f) for f in self.__dataclass_fields__}


@dataclass
class JobEvent:
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:6]}")
    job_id: str = ""
    workspace_id: str = ""
    event_type: str = ""
    timestamp: str = field(default_factory=_ts)
    message: str = ""
    status: str = ""
    progress: dict = field(default_factory=dict)
    run_id: str = ""
    trace_id: str = ""
    artifact_id: str = ""
    metadata: dict = field(default_factory=dict)
    redaction_applied: bool = False

    def as_dict(self): return {f: getattr(self, f) for f in self.__dataclass_fields__}
