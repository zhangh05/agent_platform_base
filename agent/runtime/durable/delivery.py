# agent/runtime/durable/delivery.py
"""Phase 12: Delivery / GitOps / Change Closure.

Delivery modes: code, diagnosis, report, data_processing, artifact_generation.
Validation gates enforce: no unvalidated success, no destructive without rollback, no git auto-commit.
"""

from __future__ import annotations
import json
import logging
import uuid
import time as _time
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_repo_dir(ws_id: str) -> str:
    """Resolve the git repo directory for a workspace. Falls back to CWD."""
    workspaces_dir = Path("workspaces") / (ws_id if ws_id else "_invalid")
    if workspaces_dir.is_dir():
        return str(workspaces_dir.resolve())
    return os.getcwd()
from typing import Optional, Literal
from storage.delivery_store import read_rollback_plan, save_rollback_plan as persist_rollback_plan
from agent.runtime.utils import now_iso

DeliveryMode = Literal["code","diagnosis","report","data_processing","artifact_generation"]

def _now(): return now_iso()
def _rid(): return f"rb-{uuid.uuid4().hex[:8]}"
def _aid(): return f"art-{uuid.uuid4().hex[:8]}"

# ── Rollback Plan ──

@dataclass
class RollbackPlan:
    rollback_id: str = field(default_factory=_rid)
    task_id: str = ""
    workspace_id: str = ""
    strategy: str = ""
    steps: list = field(default_factory=list)
    required_artifacts: list = field(default_factory=list)
    validation_after_rollback: str = ""
    risk: str = ""
    created_at: str = ""
    def __post_init__(self):
        if not self.created_at: self.created_at = _now()
    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, d): return cls(**{k:v for k,v in d.items() if k in cls.__dataclass_fields__})


# ── Delivery Artifact ──

@dataclass
class DeliveryArtifact:
    artifact_id: str = field(default_factory=_aid)
    task_id: str = ""
    delivery_mode: str = ""
    type: str = ""  # diff|patch|config_bundle|audit_report|validation_report|rollback_plan|markdown_report
    source_refs: list = field(default_factory=list)
    created_at: str = ""
    def __post_init__(self):
        if not self.created_at: self.created_at = _now()
    def to_dict(self): return asdict(self)


# ── Validation Gate ──

VALIDATION_REQUIREMENTS = {
    "code": ["test_passed", "build_passed", "lint_passed"],
    "network_change": ["precheck", "approval", "rollback_plan", "postcheck"],
    "diagnosis": ["evidence_collected"],
    "report": ["artifact_generated"],
    "data_processing": ["validation_summary"],
    "artifact_generation": ["artifact_id"],
}

DESTRUCTIVE_MODES = {"network_change", "code"}


def validate_delivery(mode: DeliveryMode, checks: dict) -> tuple[bool, list[str]]:
    """Validate delivery readiness. Returns (ok, missing_checks)."""
    if mode not in VALIDATION_REQUIREMENTS:
        return False, ["unknown_delivery_mode"]
    required = VALIDATION_REQUIREMENTS[mode]
    missing = [c for c in required if not checks.get(c)]
    return len(missing) == 0, missing


def requires_rollback(mode: DeliveryMode) -> bool:
    return mode in DESTRUCTIVE_MODES


# ── Rollback Plan Persistence ──

def save_rollback_plan(plan: RollbackPlan):
    persist_rollback_plan(plan.workspace_id, plan.rollback_id, plan.to_dict())

def get_rollback_plan(ws_id: str, rb_id: str) -> Optional[dict]:
    return read_rollback_plan(ws_id, rb_id)


# ── Audit Report ──

def build_audit_report(task_id: str, ws_id: str) -> dict:
    """Build a comprehensive audit report from all runtime data."""
    report = {"task_id": task_id, "workspace_id": ws_id, "generated_at": _now()}
    try:
        from agent.runtime.durable.store import get_task, get_events
        from agent.runtime.durable.trajectory import build_trajectory, evaluate_trajectory
        task = get_task(ws_id, task_id)
        if task:
            report["task_metadata"] = {
                "session_id": task.session_id, "run_id": task.run_id,
                "user_goal": task.user_goal, "status": task.status,
                "created_at": task.created_at, "updated_at": task.updated_at,
            }
        traj = build_trajectory(task_id, ws_id)
        if traj:
            report["trajectory"] = {"score": evaluate_trajectory(traj.to_dict() if hasattr(traj,'to_dict') else {})}
    except Exception as e:
        report["error"] = f"Failed to build audit report: {str(e)[:200]}"
    return report


def export_audit_report_markdown(task_id: str, ws_id: str) -> str:
    report = build_audit_report(task_id, ws_id)
    meta = report.get("task_metadata", {})
    traj = report.get("trajectory", {})
    lines = [
        f"# Audit Report — {task_id[:8]}",
        f"Workspace: {ws_id}",
        f"Status: {meta.get('status','unknown')}",
        f"Goal: {meta.get('user_goal','')[:200]}",
        f"Created: {meta.get('created_at','')}",
        f"",
        f"## Trajectory Evaluation",
        f"Score: {traj.get('score',{}).get('score','N/A')}/10",
        f"Issues: {', '.join(traj.get('score',{}).get('issues',[])) or 'none'}",
        f"",
        f"--- Generated {report['generated_at']}",
    ]
    return "\n".join(lines)


# ── GitOps Safety ──

def git_status_check(ws_id: str) -> dict:
    """Check real git status for the workspace directory.

    v3.10: Uses subprocess to run `git status --porcelain` for actual repo state.
    Falls back to unknown if git is not available or directory is not a repo.
    """
    result = {"ok": True, "workspace": ws_id, "dirty": False, "branch": "unknown",
              "changed_files": [], "untracked": []}
    try:
        import subprocess, os
        repo_dir = _resolve_repo_dir(ws_id)
        # Check if it's a git repo
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10, cwd=repo_dir,
        )
        if r.returncode != 0:
            result["status"] = "not_a_repo"
            return result

        # Get branch
        r2 = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=repo_dir,
        )
        result["branch"] = r2.stdout.strip()

        # Get porcelain status
        r3 = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, cwd=repo_dir,
        )
        lines = [l for l in r3.stdout.split("\n") if l.strip()]
        result["dirty"] = len(lines) > 0
        result["changed_files"] = [l[3:] for l in lines if l[:2].strip()]
        result["untracked"] = [l[3:] for l in lines if l.startswith("??")]

        from storage.events import publish
        publish(ws_id, "git", "status_checked", result["branch"])
    except FileNotFoundError:
        result["status"] = "git_not_available"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)[:200]
    return result


def git_commit(ws_id: str, message: str = "", confirm: bool = False) -> dict:
    """Real git commit (subprocess). Requires confirm=true."""
    if not confirm:
        return {"ok": False, "error": "confirm=true required to commit"}
    if not message:
        return {"ok": False, "error": "commit message required"}
    try:
        import subprocess, uuid
        repo_dir = _resolve_repo_dir(ws_id)
        r = subprocess.run(
            ["git", "add", "-A"],
            capture_output=True, text=True, timeout=30, cwd=repo_dir,
        )
        r2 = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, timeout=30, cwd=repo_dir,
        )
        if r2.returncode != 0:
            return {"ok": False, "error": r2.stderr.strip()[:500] or "commit failed"}
        from storage.events import publish
        publish(ws_id, "git", "committed", message[:80])
        return {"ok": True, "message": "committed", "commit_message": message[:200],
                "output": r2.stdout.strip()[:500]}
    except FileNotFoundError:
        return {"ok": False, "error": "git not available"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def git_push(ws_id: str, remote: str = "origin", confirm: bool = False) -> dict:
    """Real git push (subprocess). Requires confirm=true and will NOT force-push."""
    if not confirm:
        return {"ok": False, "error": "confirm=true required to push"}
    try:
        import subprocess, uuid
        repo_dir = _resolve_repo_dir(ws_id)
        # Get current branch
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=repo_dir,
        )
        branch = r.stdout.strip()
        # Push (no force)
        r2 = subprocess.run(
            ["git", "push", remote, branch],
            capture_output=True, text=True, timeout=60, cwd=repo_dir,
        )
        if r2.returncode != 0:
            return {"ok": False, "error": r2.stderr.strip()[:500] or "push failed"}
        from storage.events import publish
        publish(ws_id, "git", "pushed", f"{remote}/{branch}")
        return {"ok": True, "remote": remote, "branch": branch,
                "message": "pushed", "output": r2.stdout.strip()[:500]}
    except FileNotFoundError:
        return {"ok": False, "error": "git not available"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
