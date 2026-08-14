import json
from pathlib import Path


def test_audit_sidecar_upserts_under_workspace_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from agent.runtime.audit_record import write_audit_record

    audit_id = write_audit_record("default", "run-1", {
        "status": "unknown",
        "tool_calls": [{"tool_name": "workspace.file", "arguments": {"redacted": True}}],
    })
    assert audit_id == "audit_run-1"
    path = Path(str(tmp_path / "workspaces")) / "default" / "audits" / "audit_run-1.json"
    record = json.loads(path.read_text())
    assert record["status"] == "unknown"
    assert record["workspace_id"] == "default"

    write_audit_record("default", "run-1", {"status": "ok", "tool_calls": []})
    assert json.loads(path.read_text())["status"] == "ok"


def test_audit_sidecar_redacts_and_cannot_override_record_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from agent.runtime.audit_record import write_audit_record

    write_audit_record("default", "run-1", {
        "schema": "attacker.schema",
        "workspace_id": "other",
        "run_id": "other-run",
        "metadata": {"api_token": "sk-test-secret"},
        "warnings": ["password=plain-text"],
    })
    path = tmp_path / "workspaces" / "default" / "audits" / "audit_run-1.json"
    record = json.loads(path.read_text())
    assert record["schema"] == "lzcore.audit_record.v1"
    assert record["workspace_id"] == "default"
    assert record["run_id"] == "run-1"
    assert "sk-test-secret" not in path.read_text()
    assert "plain-text" not in path.read_text()
