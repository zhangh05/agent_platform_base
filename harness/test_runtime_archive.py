"""Runtime Archive Tests — v0.1"""
import os
import json
import sys
import pytest
from harness.conftest import read_frontend_source_text

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestArchivePolicy:
    def test_default_policy_exists(self):
        from core.runtime.archive import default_archive_policy
        policy = default_archive_policy()
        assert policy.runs_older_than_days == 30
        assert policy.temp_older_than_days == 7

    def test_policy_as_dict(self):
        from core.runtime.archive import default_archive_policy
        d = default_archive_policy().as_dict()
        assert "runs_older_than_days" in d


class TestPreviewArchive:
    def test_preview_no_move(self):
        from core.runtime.archive import preview_archive_candidates
        preview = preview_archive_candidates("default")
        assert preview.dry_run is True
        assert preview.moved_counts == {}

    def test_preview_structure(self):
        from core.runtime.archive import preview_archive_candidates
        preview = preview_archive_candidates("default")
        assert preview.workspace_id == "default"
        assert isinstance(preview.candidate_counts, dict)
        assert isinstance(preview.blocked_items, list)

    def test_missing_workspace(self):
        from core.runtime.archive import preview_archive_candidates
        preview = preview_archive_candidates("nonexistent_ws_xyz")
        assert len(preview.warnings) > 0

    def test_preview_no_absolute_path(self):
        from core.runtime.archive import preview_archive_candidates
        preview = preview_archive_candidates("default")
        output = str(preview.as_dict())
        assert "/Users/" not in output

    def test_session_owned_run_and_trace_are_never_archive_candidates(self, temp_dirs):
        from core.runtime.archive import ArchivePolicy, preview_archive_candidates
        from storage.paths import workspace_root
        from storage.workspace_store import ensure_workspace

        workspace_id = "archive_session_refs"
        ensure_workspace(workspace_id)
        root = workspace_root(workspace_id)
        (root / "runs").mkdir(parents=True, exist_ok=True)
        (root / "sessions").mkdir(parents=True, exist_ok=True)
        run = root / "runs" / "run-owned.json"
        trace = root / "runs" / "run-owned.trace.json"
        run.write_text('{"run_id":"run-owned","artifact_refs":[]}', encoding="utf-8")
        trace.write_text('{"run_id":"run-owned"}', encoding="utf-8")
        (root / "sessions" / "session-1.json").write_text(
            '{"session_id":"session-1","run_ids":["run-owned"]}', encoding="utf-8",
        )
        old = 1_600_000_000
        os.utime(run, (old, old))
        os.utime(trace, (old, old))

        preview = preview_archive_candidates(
            workspace_id,
            ArchivePolicy(runs_older_than_days=1, traces_older_than_days=1),
        )
        assert preview.candidates == []
        assert {item["path"] for item in preview.blocked_items} == {
            "run-owned.json", "run-owned.trace.json",
        }


class TestApplyArchive:
    def test_apply_default_dry_run(self):
        from core.runtime.archive import apply_archive
        result = apply_archive("default")
        assert result.dry_run is True

    def test_apply_no_confirm_blocked(self):
        from core.runtime.archive import apply_archive
        result = apply_archive("default", dry_run=False, confirm=False)
        assert "BLOCKED" in str(result.warnings)

    def test_apply_with_confirm(self):
        from core.runtime.archive import apply_archive
        result = apply_archive("default", dry_run=False, confirm=True)
        assert result.dry_run is False
        # Should have moved some expired items or 0 if none qualify
        assert isinstance(result.moved_counts, dict)

    def test_archive_creates_audit(self):
        from core.runtime.archive import apply_archive, get_archive_audits
        apply_archive("default", dry_run=True)
        audits = get_archive_audits("default")
        assert len(audits) > 0

    def test_archive_audit_no_secrets(self):
        from core.runtime.archive import get_archive_audits
        audits = get_archive_audits("default")
        for a in audits:
            a_str = str(a).lower()
            for secret in ("password", "token", "community"):
                assert secret not in a_str

    def test_archive_audit_no_absolute_path(self):
        from core.runtime.archive import get_archive_audits
        audits = get_archive_audits("default")
        for a in audits:
            assert "/Users/" not in str(a)

    def test_get_single_audit(self):
        from core.runtime.archive import apply_archive, get_archive_audits, get_archive_audit
        apply_archive("default", dry_run=True)
        audits = get_archive_audits("default")
        if audits:
            aid = audits[0]["audit_id"]
            detail = get_archive_audit("default", aid)
            assert detail["audit_id"] == aid

    def test_archive_structure_correct(self):
        """Archived items should honor workspace boundary."""
        from core.runtime.archive import apply_archive, preview_archive_candidates
        preview = preview_archive_candidates("default")
        # All candidates should have type and name
        for c in preview.candidates:
            assert "type" in c
            assert "name" in c

    def test_excluded_workspace_blocked(self):
        from core.runtime.archive import preview_archive_candidates
        preview = preview_archive_candidates("../../../etc")
        assert len(preview.warnings) > 0

    def test_current_run_trace_job_and_temp_layout_is_archived(self, temp_dirs):
        from core.runtime.archive import ArchivePolicy, apply_archive, preview_archive_candidates
        from jobs.schemas import JobRecord
        from jobs.store import create_job, update_job
        from storage.paths import workspace_root
        from storage.run_record_store import save_trace_record
        from storage.workspace_store import ensure_workspace

        ws = "archive_current"
        ensure_workspace(ws)
        root = workspace_root(ws)
        run_path = root / "runs" / "run_old.json"
        run_path.write_text(json.dumps({"run_id": "run_old", "workspace_id": ws}), encoding="utf-8")
        save_trace_record(ws, "run_old", {"run_id": "run_old"})
        trace_path = root / "runs" / "run_old.trace.json"
        job = create_job(JobRecord(workspace_id=ws, job_type="agent_run"))
        update_job(ws, job.job_id, {"status": "succeeded"})
        job_path = root / "jobs" / job.job_id / f"{job.job_id}.json"
        temp_path = root / "files" / "tmp" / "old.tmp"
        temp_path.write_text("temporary", encoding="utf-8")
        old = 1_600_000_000
        for path in (run_path, trace_path, job_path, temp_path):
            os.utime(path, (old, old))

        policy = ArchivePolicy(
            runs_older_than_days=1,
            traces_older_than_days=1,
            jobs_older_than_days=1,
            temp_older_than_days=1,
        )
        preview = preview_archive_candidates(ws, policy)
        assert {item["type"] for item in preview.candidates} == {"run", "trace", "job", "temp"}

        result = apply_archive(ws, policy, dry_run=False, confirm=True)
        assert result.moved_counts == {"run": 1, "trace": 1, "job": 1, "temp": 1}
        assert not run_path.exists()
        assert not trace_path.exists()
        assert not job_path.exists()
        assert not temp_path.exists()

    def test_archived_item_can_be_listed_and_restored(self, temp_dirs):
        from core.runtime.archive import list_archived_items, restore_archived_item
        from storage.paths import workspace_root
        from storage.workspace_store import ensure_workspace

        workspace_id = "archive_restore"
        ensure_workspace(workspace_id)
        root = workspace_root(workspace_id)
        archived = root / "sys" / "archives" / "2026-07" / "runs" / "run-old.json"
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_text('{"run_id":"run-old"}', encoding="utf-8")

        items = list_archived_items(workspace_id)
        assert [(item["month"], item["kind"], item["name"]) for item in items] == [
            ("2026-07", "runs", "run-old.json"),
        ]
        result = restore_archived_item(
            workspace_id, month="2026-07", kind="runs", name="run-old.json", confirm=True,
        )
        assert result["ok"] is True
        assert (root / "runs" / "run-old.json").is_file()
        assert not archived.exists()

    def test_archive_restore_rejects_path_traversal(self):
        from core.runtime.archive import restore_archived_item

        result = restore_archived_item(
            "default", month="2026-07", kind="runs", name="../secret", confirm=True,
        )
        assert result == {"ok": False, "error": "invalid_name"}


class TestUIArchive:
    def test_ui_has_archive_preview(self):
        html = read_frontend_source_text()
        assert "archive" in html and "previewSessions" in html

    def test_ui_no_default_archive_button(self):
        html = read_frontend_source_text()
        # Should not have a default delete/archive action
        assert "应用归档" not in html or "confirm" in html.lower()

    def test_ui_no_unverified_execution_claim(self):
        html = read_frontend_source_text()
        assert "已修改生产环境" not in html

    def test_ui_no_absolute_path(self):
        html = read_frontend_source_text()
        assert "/Users/" not in html
