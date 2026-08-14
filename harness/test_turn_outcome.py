import types


def item(ok, may_continue=False):
    return types.SimpleNamespace(ok=ok, execution_may_continue=may_continue)


def test_derive_execution_outcome_preserves_known_states():
    from core.runtime_engine.turn_outcome import derive_execution_outcome
    assert derive_execution_outcome([]) == "complete"
    assert derive_execution_outcome([item(True)]) == "complete"
    assert derive_execution_outcome([item(False)]) == "failed"
    assert derive_execution_outcome([item(True), item(False)]) == "partial"
    assert derive_execution_outcome([item(True), item(False, may_continue=True)]) == "unknown"
