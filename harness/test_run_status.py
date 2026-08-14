"""Run status projection and lifecycle contracts.

Bug: storage.run_record_store._safe_status previously read result.get("ok"), but
`result` at the call site was `state.skill_results` (the tool skill payload
dict, with no `ok` key). So status was always "ok" even when the run failed,
and only `ok` (boolean) was set correctly by _merge_result_projection — the
two fields got out of sync and the UI showed "成功" in the list while the
detail page said "failed".

These tests verify:
  1. _safe_status now reads state.result_ok / state.result_errors
  2. _safe_status ignores caller result status and uses current state projection
  3. _safe_status falls through to "ok" only when there's no error signal
  4. _merge_result_projection reconciles `status` with the real `ok` field
"""
from types import SimpleNamespace
import json


def _ctx():
    return {"llm": {}, "capability_id": "", "memory_written": False, "workspace_updated": False}


def _state(**overrides):
    base = dict(
        request_id="r1", session_id="s1", created_at="2026-01-01T00:00:00",
        user_input="hello", intent="", context=_ctx(),
        runtime_mode="ssot_runtime",
        final_response="", warnings=[], trace_id="", error=None,
        result_ok=None, result_errors=[],
        skill_results={}, tool_results={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_safe_status_reads_explicit_result_ok_false():
    from storage.run_record_store import _safe_status
    # Real AgentResult.ok = False → status must be "error"
    s = _state(result_ok=False)
    assert _safe_status(s, {}) == "error", \
        "status should be 'error' when state.result_ok is False"


def test_safe_status_reads_result_errors_nonempty():
    from storage.run_record_store import _safe_status
    # AgentResult.errors non-empty → status must be "error"
    s = _state(result_ok=True, result_errors=["boom"])
    assert _safe_status(s, {}) == "error", \
        "status should be 'error' when state.result_errors is non-empty"


def test_safe_status_ok_when_all_clear():
    from storage.run_record_store import _safe_status
    s = _state(result_ok=True, result_errors=[])
    assert _safe_status(s, {}) == "ok"


def test_safe_status_projects_partial_execution_outcome():
    from storage.run_record_store import _safe_status

    s = _state(result_ok=True, result_errors=[], execution_outcome="partial")
    assert _safe_status(s, {}) == "partial"


def test_safe_status_ignores_dict_ok_without_state_projection():
    from storage.run_record_store import _safe_status
    s = _state()  # no result_ok / result_errors
    assert _safe_status(s, {"ok": False}) == "ok"


def test_safe_status_planned_overrides_ok():
    """If capability_status=='planned', status is 'planned' regardless of ok."""
    from storage.run_record_store import _safe_status
    s = _state(
        result_ok=True,
        context={"llm": {}, "capability_id": "", "memory_written": False,
                 "workspace_updated": False, "capability_status": "planned"},
    )
    assert _safe_status(s, {}) == "planned"


def test_safe_status_state_error_overrides_everything():
    """state.error is the highest-priority signal."""
    from storage.run_record_store import _safe_status
    s = _state(result_ok=True, error="llm timeout")
    assert _safe_status(s, {}) == "error"


def test_merge_result_projection_reconciles_status_on_failure(monkeypatch, tmp_path):
    """The full write → merge path must end with status=='error' for failed runs."""
    from agent.runtime import turn_persistence as tp
    import storage.run_record_store as rs
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "default" / "runs").mkdir(parents=True, exist_ok=True)

    # Pretend the run was a failure: ok=False, errors=["something broke"]
    class _FakeResult:
        ok = False
        errors = ["something broke"]
        warnings = []
        tool_calls = []
        tool_decision = {}
        no_tool_reason = ""
        trace_id = "tr-1"
        final_response = ""
        def to_dict(self):
            return {"ok": False, "errors": ["something broke"], "turn_id": "r-fail",
                    "trace_id": "tr-1", "tool_calls": [], "tool_decision": {},
                    "no_tool_reason": "", "metadata": {}}

    state = _state(result_ok=False, result_errors=["something broke"], trace_id="tr-1")
    state.error = "something broke"

    class _FakeTurn:
        turn_id = "r-fail"
        op = None
        context = {}

    # write_run_record returns the run_id and writes the file
    state.request_id = "r-fail"  # write_run_record uses request_id as run_id
    rid = rs.write_run_record(state, "default")
    assert rid == "r-fail"

    # Then _merge_result_projection runs and writes the real result data
    tp._merge_result_projection(rid, "default", _FakeResult(), context=None)

    # Now read back the file
    from pathlib import Path
    import json
    rec = json.loads((tmp_path / "default" / "runs" / f"{rid}.json").read_text())
    assert rec["ok"] is False, f"ok must be False, got {rec['ok']}"
    assert rec["status"] == "error", (
        f"BUG STILL PRESENT: status={rec['status']!r} but ok=False. "
        f"This is exactly the bug the user reported — list says '成功', detail says 'failed'."
    )


def test_merge_result_projection_reconciles_status_on_success(monkeypatch, tmp_path):
    """Successful run must end with status=='ok' AND ok==True."""
    from agent.runtime import turn_persistence as tp
    import storage.run_record_store as rs
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "default" / "runs").mkdir(parents=True, exist_ok=True)

    class _FakeResult:
        ok = True
        errors = []
        warnings = []
        tool_calls = []
        tool_decision = {}
        no_tool_reason = ""
        trace_id = "tr-2"
        final_response = "all good"
        def to_dict(self):
            return {"ok": True, "errors": [], "turn_id": "r-ok",
                    "trace_id": "tr-2", "tool_calls": [], "tool_decision": {},
                    "no_tool_reason": "", "metadata": {}}

    state = _state(result_ok=True, result_errors=[], trace_id="tr-2")
    state.request_id = "r-ok"

    class _FakeTurn:
        turn_id = "r-ok"
        op = None
        context = {}

    rid = rs.write_run_record(state, "default")
    tp._merge_result_projection(rid, "default", _FakeResult(), context=None)

    rec = json.loads((tmp_path / "default" / "runs" / f"{rid}.json").read_text())
    assert rec["ok"] is True
    assert rec["status"] == "ok"


def test_recovered_tool_failures_do_not_mark_completed_task_partial(monkeypatch, tmp_path):
    from agent.runtime import turn_persistence as tp
    import storage.run_record_store as rs

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "default" / "runs").mkdir(parents=True, exist_ok=True)

    class _FakeResult:
        ok = True
        errors = []
        warnings = ["partial_tool_failure: two failed, one succeeded"]
        tool_calls = []
        tool_decision = {}
        no_tool_reason = ""
        trace_id = "tr-recovered"
        final_response = "172.19.0.2"

        def to_dict(self):
            return {
                "ok": True,
                "errors": [],
                "warnings": self.warnings,
                "turn_id": "r-recovered",
                "trace_id": self.trace_id,
                "tool_calls": [],
                "tool_decision": {},
                "no_tool_reason": "",
                "metadata": {
                    "execution_outcome": "complete",
                    "tool_execution_outcome": "partial",
                },
            }

    state = _state(
        result_ok=True,
        result_errors=[],
        execution_outcome="complete",
        tool_execution_outcome="partial",
        warnings=_FakeResult.warnings,
    )
    state.request_id = "r-recovered"
    rid = rs.write_run_record(state, "default")
    tp._merge_result_projection(rid, "default", _FakeResult(), context=None)

    rec = json.loads((tmp_path / "default" / "runs" / f"{rid}.json").read_text())
    assert rec["status"] == "ok"
    assert rec["execution_outcome"] == "complete"
    assert rec["tool_execution_outcome"] == "partial"
    assert rec["warning_count"] == 1


def test_merge_result_projection_preserves_partial_status(monkeypatch, tmp_path):
    from agent.runtime import turn_persistence as tp
    import storage.run_record_store as rs

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "default" / "runs").mkdir(parents=True, exist_ok=True)

    class _FakeResult:
        ok = True
        errors = []
        warnings = ["partial_tool_failure: one failed"]
        tool_calls = []
        tool_decision = {}
        no_tool_reason = ""
        trace_id = "tr-partial"
        final_response = "usable partial result"

        def to_dict(self):
            return {
                "ok": True,
                "errors": [],
                "warnings": self.warnings,
                "turn_id": "r-partial",
                "trace_id": self.trace_id,
                "tool_calls": [],
                "tool_decision": {},
                "no_tool_reason": "",
                "metadata": {"execution_outcome": "partial"},
            }

    state = _state(
        result_ok=True,
        result_errors=[],
        execution_outcome="partial",
        warnings=_FakeResult.warnings,
    )
    state.request_id = "r-partial"
    rid = rs.write_run_record(state, "default")
    tp._merge_result_projection(rid, "default", _FakeResult(), context=None)

    rec = json.loads((tmp_path / "default" / "runs" / f"{rid}.json").read_text())
    assert rec["ok"] is True
    assert rec["status"] == "partial"
    assert rec["execution_outcome"] == "partial"


def test_run_record_warning_count_uses_agent_result_warnings(monkeypatch, tmp_path):
    from agent.runtime import turn_persistence as tp
    import storage.run_record_store as rs

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "default" / "runs").mkdir(parents=True, exist_ok=True)

    class _FakeResult:
        ok = True
        errors = []
        warnings = ["partial_tool_failure: one failed"]
        tool_calls = []
        tool_decision = {}
        no_tool_reason = ""
        trace_id = "tr-warn"
        final_response = "partial result"

        def to_dict(self):
            return {
                "ok": True, "errors": [], "warnings": self.warnings,
                "turn_id": "r-warn", "trace_id": self.trace_id,
                "tool_calls": [], "tool_decision": {}, "no_tool_reason": "",
                "metadata": {},
            }

    state = _state(result_ok=True, result_errors=[], warnings=_FakeResult.warnings)
    state.request_id = "r-warn"
    rid = rs.write_run_record(state, "default")
    tp._merge_result_projection(rid, "default", _FakeResult(), context=None)

    rec = json.loads((tmp_path / "default" / "runs" / f"{rid}.json").read_text())
    assert rec["warning_count"] == 1
    assert rec["result_counts"]["warnings"] == 1
    assert rec["warnings"] == _FakeResult.warnings


def test_run_projection_keeps_latest_tracking_poll_only():
    from agent.runtime.turn_persistence import _safe_tool_calls

    calls = [
        {"call_id": "spawn-a", "tool_id": "agent.manage", "ok": True, "summary": "started"},
        {"call_id": "spawn-a_track_1", "tool_id": "agent.manage", "ok": True, "summary": "running"},
        {"call_id": "spawn-a_track_2", "tool_id": "agent.manage", "ok": True, "summary": "completed"},
        {"call_id": "other", "tool_id": "web.manage", "ok": True, "summary": "done"},
    ]

    projected = _safe_tool_calls(calls)

    assert [call["call_id"] for call in projected] == [
        "spawn-a", "spawn-a_track_2", "other",
    ]
    assert projected[1]["summary"] == "completed"


def test_run_projection_preserves_orchestration_json_types():
    from agent.runtime.turn_persistence import _safe_tool_calls

    projected = _safe_tool_calls([{
        "call_id": "call-a",
        "tool_id": "data.manage",
        "ok": True,
        "metadata": {
            "orchestration": {
                "step_id": "parse",
                "layer": 1,
                "parallel": False,
                "depends_on": [],
            },
        },
    }])
    orchestration = projected[0]["metadata"]["orchestration"]
    assert orchestration == {
        "step_id": "parse", "layer": 1, "parallel": False, "depends_on": [],
    }


def test_run_projection_keeps_full_query_loop_node_budget():
    from agent.runtime.turn_persistence import _safe_tool_calls

    calls = [
        {"call_id": f"call-{index}", "tool_id": "system.manage", "ok": True}
        for index in range(30)
    ]
    assert len(_safe_tool_calls(calls)) == 30


def test_trace_uses_recorded_parallel_steps_not_topological_layer_width():
    from agent.runtime.ssot_runtime import _project_events

    runtime_result = SimpleNamespace(
        metadata={
            "orchestration_batches": [{
                "layers": [["read-before", "write", "read-after"]],
                "parallel_steps": [[]],
            }],
        },
        node_results={},
    )
    event = _project_events(runtime_result, "trace", "turn")[0]
    assert event["metadata"]["parallel"] is False
    assert event["metadata"]["parallel_steps"] == []


def test_persist_run_record_uses_result_llm_metadata(monkeypatch, tmp_path):
    """Run-store llm_metadata must mirror AgentResult.metadata['llm']."""
    from agent.runtime.turn_persistence import persist_run_record

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "default" / "runs").mkdir(parents=True, exist_ok=True)

    class _Session:
        session_id = "s-llm"
        workspace_id = "default"
        is_sub_agent = False

    class _Turn:
        turn_id = "r-llm"
        op = SimpleNamespace(user_input="什么意思？")
        context = {}

    class _Result:
        ok = True
        final_response = "解释上一轮结果"
        warnings = []
        errors = []
        trace_id = "tr-llm"
        tool_calls = []
        tool_decision = {}
        no_tool_reason = ""
        events = []
        metadata = {
            "llm": {
                "used": True,
                "provider": "test-provider",
                "model": "test-model",
                "task": "assistant_chat",
            }
        }

        def to_dict(self):
            return {
                "ok": self.ok,
                "turn_id": _Turn.turn_id,
                "trace_id": self.trace_id,
                "tool_calls": [],
                "tool_decision": {},
                "no_tool_reason": "",
                "metadata": self.metadata,
                "errors": [],
            }

    context = SimpleNamespace(metadata={})
    persist_run_record(_Session(), _Turn(), _Result(), context)

    rec = json.loads((tmp_path / "default" / "runs" / "r-llm.json").read_text())
    assert rec["runtime_mode"] == "ssot_runtime"
    assert rec["llm_metadata"]["used"] is True
    assert rec["llm_metadata"]["provider"] == "test-provider"
    assert rec["llm_metadata"]["model"] == "test-model"


def test_safe_status_projects_unknown_before_generic_error_state():
    from storage.run_record_store import _safe_status

    state = SimpleNamespace(
        context={},
        error="runtime result is unknown",
        result_ok=False,
        result_errors=["runtime result is unknown"],
        execution_outcome="unknown",
    )
    assert _safe_status(state, {}) == "unknown"
