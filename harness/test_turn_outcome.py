import types


def item(ok, may_continue=False):
    return types.SimpleNamespace(ok=ok, execution_may_continue=may_continue)


def test_derive_tool_execution_outcome_preserves_attempt_states():
    from core.runtime_engine.turn_outcome import derive_tool_execution_outcome
    assert derive_tool_execution_outcome([]) == "complete"
    assert derive_tool_execution_outcome([item(True)]) == "complete"
    assert derive_tool_execution_outcome([item(False)]) == "failed"
    assert derive_tool_execution_outcome([item(True), item(False)]) == "partial"
    assert derive_tool_execution_outcome([item(True), item(False, may_continue=True)]) == "unknown"


def test_derive_execution_outcome_treats_recovered_attempt_as_complete():
    from core.runtime_engine.turn_outcome import derive_execution_outcome
    assert derive_execution_outcome([]) == "complete"
    assert derive_execution_outcome([item(True)]) == "complete"
    assert derive_execution_outcome([item(False)]) == "failed"
    assert derive_execution_outcome([item(True), item(False)]) == "complete"
    assert derive_execution_outcome([item(True), item(False, may_continue=True)]) == "unknown"


def test_derive_execution_outcome_keeps_real_terminal_blockers_partial():
    from core.runtime_engine.turn_outcome import derive_execution_outcome
    results = [item(True), item(False)]
    assert derive_execution_outcome(results, terminal_error="max_iterations") == "partial"
    assert derive_execution_outcome(
        results,
        goal_assertions={"required": True, "status": "failed"},
    ) == "partial"
