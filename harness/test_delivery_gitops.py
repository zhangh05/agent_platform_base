# Delivery and GitOps contracts.
"""Phase 12: Delivery / GitOps / Change Closure tests."""

import pytest, uuid
from agent.runtime.durable.delivery import (
    validate_delivery, requires_rollback,
    save_rollback_plan, get_rollback_plan,
    build_audit_report, export_audit_report_markdown,
    git_status_check, git_commit, git_push,
    RollbackPlan, DeliveryArtifact, VALIDATION_REQUIREMENTS,
)


class TestValidationGate:
    def test_code_mode_requires_validation(self):
        ok, missing = validate_delivery("code", {})
        assert not ok
        assert len(missing) >= 1

    def test_code_mode_passes_with_validation(self):
        ok, missing = validate_delivery("code", {
            "test_passed": True, "build_passed": True, "lint_passed": True,
        })
        assert ok
        assert len(missing) == 0

    def test_network_change_requires_rollback(self):
        ok, missing = validate_delivery("network_change", {})
        assert not ok
        assert "rollback_plan" in missing

    def test_network_change_with_all_checks_passes(self):
        ok, missing = validate_delivery("network_change", {
            "precheck": True, "approval": True, "rollback_plan": True, "postcheck": True,
        })
        assert ok

    def test_diagnosis_requires_evidence(self):
        ok, missing = validate_delivery("diagnosis", {})
        assert not ok
        assert "evidence_collected" in missing

    def test_report_requires_artifact(self):
        ok, missing = validate_delivery("report", {})
        assert not ok
        assert "artifact_generated" in missing

    def test_destructive_modes_require_rollback(self):
        assert requires_rollback("network_change")
        assert requires_rollback("code")
        assert not requires_rollback("report")


class TestRollbackPlan:
    def test_save_and_load(self):
        ws = f"ws_rb_{uuid.uuid4().hex[:8]}"
        plan = RollbackPlan(
            task_id="t1", workspace_id=ws,
            strategy="Rollback to snapshot", steps=["restore artifact"],
            risk="medium",
        )
        save_rollback_plan(plan)
        loaded = get_rollback_plan(ws, plan.rollback_id)
        assert loaded is not None
        assert loaded["strategy"] == "Rollback to snapshot"

    def test_rollback_plan_binds_workspace(self):
        ws_a = f"ws_rba_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_rbb_{uuid.uuid4().hex[:8]}"
        plan = RollbackPlan(task_id="t1", workspace_id=ws_a)
        save_rollback_plan(plan)
        assert get_rollback_plan(ws_b, plan.rollback_id) is None


class TestAuditReport:
    def test_build_report(self):
        from agent.runtime.durable.store import save_task
        from agent.runtime.durable.models import TaskState
        ws = f"ws_ar_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws, session_id="s1", user_goal="test audit")
        task.update_status("succeeded"); save_task(task)

        report = build_audit_report(task.task_id, ws)
        assert report["task_id"] == task.task_id
        meta = report.get("task_metadata", {})
        assert meta.get("status") == "succeeded"

class TestGitOps:
    def test_commit_no_confirm_rejected(self):
        result = git_commit("ws_x", "fix bug", confirm=False)
        assert result["ok"] is False

    def test_push_no_confirm_rejected(self):
        result = git_push("ws_x", confirm=False)
        assert result["ok"] is False

    def test_commit_no_message_rejected(self):
        result = git_commit("ws_x", "", confirm=True)
        assert result["ok"] is False

    def test_git_status(self):
        result = git_status_check("ws_x")
        assert result["ok"] is True
