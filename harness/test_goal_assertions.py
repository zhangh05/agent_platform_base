import types


def make_ctx(extras):
    return types.SimpleNamespace(extras=extras)


def make_result(call_id, ok, may_continue=False):
    return types.SimpleNamespace(
        call_id=call_id,
        ok=ok,
        execution_may_continue=may_continue,
    )


def test_plain_turn_does_not_require_goal_assertion():
    from core.runtime_engine.goal_assertions import evaluate_goal_assertions
    outcome = evaluate_goal_assertions(make_ctx({}), [])
    assert outcome["required"] is False
    assert outcome.get("status") == "not_required"
