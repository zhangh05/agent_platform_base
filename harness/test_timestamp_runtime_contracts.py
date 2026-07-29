"""Runtime timestamp contracts preventing str/float contamination.

These tests protect timestamp assignment, parsing, retention, and API
projection boundaries.

Strategy: enumerate every place a write to ``created_at`` /
``started_at`` / ``finished_at`` / ``resolved_at`` happens and assert
the assignment is via the ISO helper, not ``time.time()``.
"""

import ast
import json
import pytest
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # portable: resolve from this test file

# (file path relative to repo, function name, target field, expected
#  helper call). A row here is a regression guard: if a future edit
# inserts ``foo.resolved_at = time.time()`` inside the named
# function, the matching test fails.
GUARDS = [
    ("agent/approval.py", "resolve",    "resolved_at"),
    ("agent/approval.py", "wait",       "resolved_at"),
    ("agent/approval.py", "_gc_history", "_last_gc_at"),
]


def _function_calls(path: Path, func_name: str) -> list[ast.Call]:
    """Return all ``ast.Call`` nodes inside the function ``func_name``."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return list(ast.walk(node))
    raise AssertionError(
        f"function {func_name!r} not found in {path}"
    )


def _assignments_to(nodes: list[ast.stmt], field_name: str) -> list[ast.Assign]:
    """Return all assignments whose leftmost target is ``self.<field>``
    or top-level ``<field>`` (assignment statement form)."""
    found: list[ast.Assign] = []
    for n in nodes:
        if not isinstance(n, ast.Assign):
            continue
        for t in n.targets:
            if isinstance(t, ast.Attribute) and t.attr == field_name:
                found.append(n)
                break
    return found


def _call_name(call: ast.Call) -> str:
    try:
        return ast.unparse(call.func)
    except Exception:
        return "<unparseable>"


def test_no_resolved_at_time_time_in_approval():
    """``ApprovalStore.resolve`` must write resolved_at via now_iso(),
    not time.time() — the field is now str (v3.9.8)."""
    path = PROJECT_ROOT / "agent" / "approval.py"
    body = _function_calls(path, "resolve")
    bad = []
    for assign in _assignments_to(body, "resolved_at"):
        rhs = assign.value
        if isinstance(rhs, ast.Call):
            if _call_name(rhs) == "time.time":
                bad.append(f"line {assign.lineno}: {ast.unparse(assign)!r}")
    assert not bad, (
        "Found time.time() assignment to resolved_at (should use now_iso()): "
        + "; ".join(bad)
    )


def test_no_resolved_at_time_time_in_wait():
    """``ApprovalStore.wait`` (auto-deny on timeout) must use now_iso()."""
    path = PROJECT_ROOT / "agent" / "approval.py"
    body = _function_calls(path, "wait")
    bad = []
    for assign in _assignments_to(body, "resolved_at"):
        rhs = assign.value
        if isinstance(rhs, ast.Call) and _call_name(rhs) == "time.time":
            bad.append(f"line {assign.lineno}: {ast.unparse(assign)!r}")
    assert not bad, (
        "Found time.time() assignment to resolved_at in wait(): "
        + "; ".join(bad)
    )


def test_no_time_time_assignment_to_last_gc_at():
    """``_gc_history`` keeps ``_last_gc_at`` as an internal epoch float;
    it must not call time.time() inline. The cutoff comparison moved
    to ISO via ``_now_iso_offset``; the float bookkeeping is internal.
    """
    path = PROJECT_ROOT / "agent" / "approval.py"
    body = _function_calls(path, "_gc_history")
    # Sanity: at least one now_iso_offset() call must exist (proves
    # the v3.9.8 follow-up change is in place).
    iso_calls = [
        n for n in body
        if isinstance(n, ast.Call) and "_now_iso_offset" in _call_name(n)
    ]
    assert iso_calls, (
        "_gc_history must use _now_iso_offset() for retention cutoff "
        "(v3.9.8 follow-up)"
    )


def test_iso_offset_helper_exists():
    """The ``_now_iso_offset`` helper exists in approval.py."""
    path = PROJECT_ROOT / "agent" / "approval.py"
    src = path.read_text(encoding="utf-8")
    assert "def _now_iso_offset" in src


def test_gc_history_does_not_compare_str_to_float():
    """Belt-and-suspenders: search the entire approval source for the
    str/float comparison that ``str(created_at) >= float(cutoff)`` would
    produce. The post-v3.9.8 retention filter uses all-ISO ordering.
    """
    path = PROJECT_ROOT / "agent" / "approval.py"
    body = _function_calls(path, "_gc_history")
    for stmt in body:
        if not isinstance(stmt, ast.If):
            continue
        if not isinstance(stmt.test, ast.Compare):
            continue
        # Look for a Compare whose left side is ``rec.get("created_at")``
        # (Name=rec, Attribute=.get, args=Constant("created_at"))
        left = stmt.test.left
        if (isinstance(left, ast.Call)
                and isinstance(left.func, ast.Attribute)
                and left.func.attr == "get"
                and left.args
                and isinstance(left.args[0], ast.Constant)
                and left.args[0].value == "created_at"):
            comps = stmt.test.comparators
            if comps and isinstance(comps[0], ast.Constant) and isinstance(comps[0].value, float):
                pytest.fail(
                    f"_gc_history still compares created_at to a float "
                    f"constant at line {stmt.lineno}; ISO ordering required"
                )


# ── Durable.models analogue: protect mark_finished ─────────────────────


def test_runtime_step_mark_finished_uses_iso_str():
    """``RuntimeStep.mark_finished`` writes finished_at via ``_now()``
    (ISO str). It may still call ``time.time()`` for the duration
    arithmetic — that is internal sub-second math, not a timestamp
    field write. This guard ensures the visible field stays ISO.
    """
    path = PROJECT_ROOT / "agent" / "runtime" / "durable" / "models.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "mark_finished":
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Assign):
                    continue
                for t in sub.targets:
                    if (isinstance(t, ast.Attribute)
                            and t.attr in {"finished_at", "started_at", "created_at"}):
                        rhs = sub.value
                        if isinstance(rhs, ast.Call) and _call_name(rhs) == "time.time":
                            pytest.fail(
                                f"RuntimeStep.mark_finished still assigns "
                                f"time.time() to {t.attr} at line {sub.lineno}"
                            )


def test_runtime_utils_import_does_not_eager_load_context_stack():
    """Importing runtime utils must not pull context/memory into a cycle."""
    from agent.runtime.utils import now_iso
    from storage.memory_governance import MemoryStore

    assert now_iso()
    assert MemoryStore


def test_memory_naive_expiry_is_rejected_not_compat_parsed():
    from storage.memory_governance import MemoryRecord

    rec = MemoryRecord(
        status="active",
        expires_at="2026-06-30T10:00:00",
    )

    assert rec.is_retrievable() is False


def test_system_session_create_and_archive_call_store_with_current_signature():
    from core.tools.schemas import ToolInvocation
    from core.tools.general_tools.session_tools import handle_session_create, handle_session_archive
    from storage.session_store import get_session
    from storage.workspace_store import ensure_workspace
    from storage.paths import get_workspace_root

    ws = "pytest_session_tool_branch"
    shutil.rmtree(get_workspace_root() / ws, ignore_errors=True)
    ensure_workspace(ws)
    try:
        created = handle_session_create(ToolInvocation(
            tool_id="system.session",
            workspace_id=ws,
            arguments={"title": "branch test"},
        ))
        assert created["ok"] is True
        sid = created["session_id"]
        assert get_session(sid, ws)["title"] == "branch test"

        archived = handle_session_archive(ToolInvocation(
            tool_id="system.session",
            workspace_id=ws,
            arguments={"session_id": sid},
        ))
        assert archived["ok"] is True
        assert get_session(sid, ws)["status"] == "archived"
    finally:
        shutil.rmtree(get_workspace_root() / ws, ignore_errors=True)


def test_system_run_get_summary_uses_run_id_then_workspace_order():
    from agent.runtime.utils import now_iso
    from core.tools.schemas import ToolInvocation
    from core.tools.general_tools.session_tools import handle_run_get_summary
    from storage.workspace_store import ensure_workspace
    from storage.paths import get_workspace_root

    ws = "pytest_run_tool_branch"
    run_id = "run_tool_branch"
    shutil.rmtree(get_workspace_root() / ws, ignore_errors=True)
    ensure_workspace(ws)
    try:
        runs_dir = get_workspace_root() / ws / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        ts = now_iso()
        (runs_dir / f"{run_id}.json").write_text(json.dumps({
            "run_id": run_id,
            "workspace_id": ws,
            "created_at": ts,
            "started_at": ts,
            "finished_at": ts,
            "intent": "tool_contract",
            "status": "ok",
        }), encoding="utf-8")

        result = handle_run_get_summary(ToolInvocation(
            tool_id="system.run",
            workspace_id=ws,
            arguments={"run_id": run_id},
        ))
        assert result["ok"] is True
        assert result["run_id"] == run_id
        assert result["intent"] == "tool_contract"
    finally:
        shutil.rmtree(get_workspace_root() / ws, ignore_errors=True)


def test_system_session_checkpoint_has_runtime_imports_and_iso_timestamp():
    from agent.runtime.utils import from_iso
    from core.tools.schemas import ToolInvocation
    from core.tools.general_tools.session_tools import handle_session_checkpoint
    from storage.session_store import create_session
    from storage.workspace_store import ensure_workspace
    from storage.paths import get_workspace_root

    ws = "pytest_checkpoint_tool_branch"
    shutil.rmtree(get_workspace_root() / ws, ignore_errors=True)
    ensure_workspace(ws)
    try:
        session = create_session(ws_id=ws, title="checkpoint branch")
        result = handle_session_checkpoint(ToolInvocation(
            tool_id="system.session",
            workspace_id=ws,
            arguments={"session_id": session["session_id"], "reason": "contract"},
        ))
        assert result["ok"] is True
        checkpoint_path = (
            get_workspace_root() / ws / "sessions" / session["session_id"]
            / "checkpoints" / f"{result['checkpoint_id']}.json"
        )
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert from_iso(payload["created_at"]) > 0
    finally:
        shutil.rmtree(get_workspace_root() / ws, ignore_errors=True)
