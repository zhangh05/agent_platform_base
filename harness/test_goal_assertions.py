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


def test_approved_turn_requires_all_bound_call_results():
    from core.runtime_engine.goal_assertions import evaluate_goal_assertions
    ctx = make_ctx({"approval_continuation_id": "cont_x", "approved_tool_call_ids": ["a", "b"]})
    outcome = evaluate_goal_assertions(ctx, [make_result("a", True), make_result("b", False)])
    assert outcome["required"] is True
    assert outcome.get("status") == "failed"
    ctx = make_ctx({"approval_continuation_id": "cont_x", "approved_tool_call_ids": ["a"]})
    outcome = evaluate_goal_assertions(ctx, [make_result("a", False, may_continue=True)])
    assert outcome.get("status") == "unknown"


def test_approved_turn_cannot_pass_with_a_missing_bound_result():
    from core.runtime_engine.goal_assertions import evaluate_goal_assertions
    ctx = make_ctx({
        "approval_continuation_id": "cont_x",
        "approved_tool_call_ids": ["a", "b"],
    })
    outcome = evaluate_goal_assertions(ctx, [make_result("a", True)])
    assertion = outcome["assertions"][0]
    assert outcome["status"] == "unknown"
    assert assertion["missing_call_keys"] == ["b"]
