from pathlib import Path
from types import SimpleNamespace


def _ctx():
    return SimpleNamespace(
        workspace_id="default",
        request_id="turn-1",
        session_id="session-1",
        extras={"risk_level": "high"},
    )


def test_operation_ledger_persists_unknown_without_arguments(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import (
        finish_operation,
        plan_operation,
        start_operation,
    )

    operation = plan_operation(_ctx(), "workspace.file", "call-1", {"token": "secret", "action": "write"})
    assert "token" not in str(operation)
    start_operation("default", operation["operation_id"])
    result = SimpleNamespace(
        ok=False,
        error="remote action may continue",
        error_code="TOOL_TIMEOUT_UNCERTAIN",
        execution_may_continue=True,
        output={"executed": True},
    )
    finished = finish_operation("default", operation["operation_id"], result)
    assert finished["status"] == "unknown"
    path = Path(str(tmp_path / "workspaces")) / "default" / "operations" / f'{operation["operation_id"]}.json'
    assert path.is_file()
    assert "secret" not in path.read_text()


def test_operation_ledger_marks_unstarted_operation_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import (
        finish_operation,
        plan_operation,
        start_operation,
    )

    operation = plan_operation(_ctx(), "workspace.file", "call-2", {"action": "write"})
    start_operation("default", operation["operation_id"])
    result = SimpleNamespace(
        ok=False,
        error="budget exhausted before start",
        error_code="TOOL_BUDGET_EXHAUSTED",
        execution_may_continue=False,
        output={"executed": False},
    )
    assert finish_operation("default", operation["operation_id"], result)["status"] == "blocked"


def test_operation_ledger_public_projection_redacts_result_details(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from core.runtime_engine.operation_ledger import finish_operation, list_operations, plan_operation, start_operation

    operation = plan_operation(_ctx(), "workspace.file", "call-public", {"token": "secret"})
    start_operation("default", operation["operation_id"])
    finish_operation("default", operation["operation_id"], SimpleNamespace(
        ok=False,
        error="token=secret remote write uncertain",
        error_code="TOOL_TIMEOUT_UNCERTAIN",
        execution_may_continue=True,
        output={"executed": True, "summary": "token=secret summary"},
    ))
    records = list_operations("default")

    assert len(records) == 1
    assert records[0]["status"] == "unknown"
    assert records[0]["operation_id"] == operation["operation_id"]
    assert "arguments_sha256" not in records[0]
    assert "secret" not in str(records[0])
    assert "[REDACTED_SECRET]" in records[0]["error"]
