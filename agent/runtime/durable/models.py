# agent/runtime/durable/models.py
"""Phase 2 data models for durable runtime state."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional, Literal
from agent.runtime.utils import now_iso, duration_ms

TaskStatus = Literal["pending","running","interrupting","waiting_approval","succeeded","failed","cancelled"]
StepKind = Literal["message","model","tool","approval","checkpoint","validation","final","error"]
StepStatus = Literal["pending","running","succeeded","failed","skipped","cancelled"]

# v3.9.10: thin alias kept for callers that historically imported
# ``_now`` from this module. Returns the unified ISO-8601 timestamp.
def _now() -> str: return now_iso()

def _next_id(prefix="evt"): return f"{prefix}-{uuid.uuid4().hex[:12]}"

@dataclass
class RuntimeStep:
    step_id: str; task_id: str; kind: StepKind = "message"; status: StepStatus = "pending"
    title: str = ""; summary: str = ""; tool_id: Optional[str] = None
    input_ref: Optional[str] = None; output_ref: Optional[str] = None
    approval_id: Optional[str] = None; started_at: str = ""; finished_at: str = ""
    # v3.9.8: duration_ms is now int milliseconds (matches ToolResult
    # and TrajectoryRecord). Float with millisecond decimal precision
    # was unnecessary; int covers up to ~2.9e6 hours without overflow.
    duration_ms: Optional[int] = None
    def mark_started(self): self.started_at = now_iso(); self.status = "running"
    def mark_finished(self, ok=True, summary=""):
        self.finished_at = now_iso(); self.status = "succeeded" if ok else "failed"
        if summary: self.summary = summary
        if self.started_at:
            try:
                self.duration_ms = duration_ms(self.started_at, self.finished_at)
            except (TypeError, ValueError):
                pass  # non-critical idempotent update

@dataclass
class RuntimeEvent:
    event_id: str; task_id: str; workspace_id: str; session_id: str; run_id: str
    step_id: str = ""; type: str = ""; status: str = ""; title: str = ""; summary: str = ""
    payload_redacted: dict = field(default_factory=dict); created_at: str = ""

@dataclass
class RuntimeCheckpoint:
    checkpoint_id: str; task_id: str; workspace_id: str; session_id: str; run_id: str
    step_id: str = ""; state_snapshot: dict = field(default_factory=dict)
    pending_action: Optional[dict] = None; artifact_refs: list = field(default_factory=list)
    created_at: str = ""

@dataclass
class TaskState:
    task_id: str; workspace_id: str; session_id: str
    run_id: str = ""; job_id: str = ""; trace_id: str = ""; user_goal: str = ""
    status: TaskStatus = "pending"; current_step_id: str = ""
    steps: list = field(default_factory=list); pending_approval_id: Optional[str] = None
    pending_action_id: str = ""; interrupted_at: str = ""
    tool_results: list = field(default_factory=list); artifact_ids: list = field(default_factory=list)
    warnings: list = field(default_factory=list); errors: list = field(default_factory=list)
    created_at: str = ""; updated_at: str = ""
    def __post_init__(self):
        if not self.created_at: self.created_at = _now()
        if not self.updated_at: self.updated_at = self.created_at
    def add_step(self, step: RuntimeStep) -> RuntimeStep:
        step.task_id = self.task_id; self.steps.append(step)
        self.current_step_id = step.step_id; self.updated_at = _now(); return step
    def update_status(self, status: TaskStatus): self.status = status; self.updated_at = _now()
    def to_dict(self) -> dict:
        d = asdict(self); d["steps"] = [asdict(s) if hasattr(s,'__dataclass_fields__') else s for s in self.steps]; return d
    @classmethod
    def from_dict(cls, d: dict) -> "TaskState":
        sr = d.pop("steps",[]); task = cls(**{k:v for k,v in d.items() if k in cls.__dataclass_fields__})
        task.steps = [RuntimeStep(**{k:v for k,v in s.items() if k in RuntimeStep.__dataclass_fields__}) if isinstance(s,dict) else s for s in sr]; return task
    @staticmethod
    def new(workspace_id, session_id, **kw) -> "TaskState":
        n = _now()
        return TaskState(task_id=kw.get("task_id",_next_id("task")), workspace_id=workspace_id,
                         session_id=session_id, run_id=kw.get("run_id",""), job_id=kw.get("job_id",""),
                         trace_id=kw.get("trace_id",""), user_goal=kw.get("user_goal",""),
                         status="pending", created_at=n, updated_at=n)
