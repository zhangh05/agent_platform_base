"""Framework-wide goal-loop regression coverage."""
from __future__ import annotations

import asyncio

from agent.llm.schemas import LLMResponse, LLMToolCall
from core.runtime_engine.engine import SSOTRuntimeEngine
from core.runtime_engine.goal_assertions import evaluate_goal_assertions
from core.runtime_engine.goal_loop import goal_loop_summary, observe_tool_round
from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
from core.runtime_engine.query_loop import StreamingToolResult
from core.runtime_engine.recovery_goals import recovery_final_gate
from core.runtime_engine.recovery_goals import install_recovery_goal
from core.runtime_engine.tool_runtime import ToolRuntime


def _ctx() -> StatelessContext:
    return StatelessContext(
        workspace_id="test", session_id="session", request_id="request",
        user_input="find the requested evidence",
    )


def _result(call_id: str, *, ok: bool, code: str = "", error: str = "") -> StreamingToolResult:
    return StreamingToolResult(
        tool_name="web.manage", call_id=call_id,
        output={"ok": ok, "error_code": code, "error": error},
        ok=ok, error=error, error_code=code,
    )


def test_recoverable_failure_installs_runtime_owned_goal():
    ctx = _ctx()
    call = LLMToolCall(
        id="bad-search", name="web.manage",
        arguments={"action": "search", "query": "bad syntax"},
    )
    observe_tool_round(ctx, [call], [_result("bad-search", ok=False, code="ARGS_INVALID", error="invalid query")], is_read_only_call=lambda _call: True)

    summary = goal_loop_summary(ctx)
    assert summary["status"] == "pending"
    assert summary["counts"]["pending"] == 1
    assert evaluate_goal_assertions(ctx, []) ["status"] == "unknown"
    assert recovery_final_gate(ctx, []).should_continue is True


def test_policy_failure_does_not_create_recovery_goal():
    ctx = _ctx()
    call = LLMToolCall(id="blocked", name="web.manage", arguments={"action": "search", "query": "x"})
    observe_tool_round(ctx, [call], [_result("blocked", ok=False, code="POLICY_BLOCKED", error="policy blocked")], is_read_only_call=lambda _call: True)
    assert goal_loop_summary(ctx)["status"] == "not_required"


def test_changed_same_capability_observation_satisfies_open_goal():
    ctx = _ctx()
    failed = LLMToolCall(id="first", name="web.manage", arguments={"action": "search", "query": "bad"})
    observe_tool_round(ctx, [failed], [_result("first", ok=False, error="provider rejected query")], is_read_only_call=lambda _call: True)
    corrected = LLMToolCall(id="second", name="web.manage", arguments={"action": "search", "query": "corrected"})
    observe_tool_round(ctx, [corrected], [_result("second", ok=True)], is_read_only_call=lambda _call: True)

    assert goal_loop_summary(ctx)["status"] == "passed"
    assert evaluate_goal_assertions(ctx, []) ["status"] == "passed"
    assert recovery_final_gate(ctx, []).should_continue is False


def test_cross_tool_recovery_requires_and_accepts_explicit_goal_link():
    ctx = _ctx()
    failed = LLMToolCall(id="web", name="web.manage", arguments={"action": "fetch", "url": "https://example.test"})
    observe_tool_round(ctx, [failed], [_result("web", ok=False, error="unsupported response")], is_read_only_call=lambda _call: True)
    goal_id = ctx.extras["recovery_goals"][0]["goal_id"]
    browser = LLMToolCall(
        id="browser", name="browser.manage",
        arguments={"action": "read", "url": "https://example.test"},
        goal_ids=[goal_id],
    )
    observe_tool_round(ctx, [browser], [StreamingToolResult(
        tool_name="browser.manage", call_id="browser", output={"ok": True}, ok=True,
    )], is_read_only_call=lambda _call: True)
    assert ctx.extras["recovery_goals"][0]["resolved_by_tool_id"] == "browser.manage"


def test_cross_tool_success_with_shared_scope_does_not_implicitly_close_goal():
    ctx = _ctx()
    failed = LLMToolCall(
        id="file", name="workspace.file",
        arguments={"action": "read", "workspace_id": "test", "path": "missing.txt"},
    )
    observe_tool_round(
        ctx, [failed], [_result("file", ok=False, error="not found")],
        is_read_only_call=lambda _call: True,
    )
    unrelated = LLMToolCall(
        id="search", name="workspace.search",
        arguments={"action": "search", "workspace_id": "test", "query": "other"},
    )
    observe_tool_round(
        ctx, [unrelated], [_result("search", ok=True)],
        is_read_only_call=lambda _call: True,
    )
    assert goal_loop_summary(ctx)["status"] == "pending"


def test_goal_target_text_is_bounded_before_runtime_persistence():
    ctx = _ctx()
    call = LLMToolCall(
        id="large", name="web.manage",
        arguments={"action": "search", "query": "x" * 5000},
    )
    observe_tool_round(
        ctx, [call], [_result("large", ok=False, error="invalid query")],
        is_read_only_call=lambda _call: True,
    )
    assert len(ctx.extras["recovery_goals"][0]["target"]["query"]) == 240


def test_side_effecting_success_cannot_satisfy_read_recovery_goal():
    ctx = _ctx()
    failed = LLMToolCall(id="read", name="workspace.file", arguments={"action": "read", "path": "x"})
    observe_tool_round(ctx, [failed], [_result("read", ok=False, error="not found")], is_read_only_call=lambda call: call.id == "read")
    goal_id = ctx.extras["recovery_goals"][0]["goal_id"]
    write = LLMToolCall(
        id="write", name="workspace.file", arguments={"action": "write", "path": "x"},
        goal_ids=[goal_id],
    )
    observe_tool_round(ctx, [write], [_result("write", ok=True)], is_read_only_call=lambda _call: False)
    assert ctx.extras["recovery_goals"][0]["status"] == "pending"


def test_three_linked_failures_block_goal_instead_of_looping_forever():
    ctx = _ctx()
    first = LLMToolCall(id="one", name="web.manage", arguments={"action": "search", "query": "one"})
    observe_tool_round(ctx, [first], [_result("one", ok=False, error="failed")], is_read_only_call=lambda _call: True)
    goal_id = ctx.extras["recovery_goals"][0]["goal_id"]
    for call_id in ("two", "three"):
        call = LLMToolCall(
            id=call_id, name="web.manage",
            arguments={"action": "search", "query": call_id}, goal_ids=[goal_id],
        )
        observe_tool_round(ctx, [call], [_result(call_id, ok=False, error="failed differently")], is_read_only_call=lambda _call: True)

    assert goal_loop_summary(ctx)["status"] == "blocked"
    assert evaluate_goal_assertions(ctx, [])["status"] == "failed"
    assert recovery_final_gate(ctx, []).should_continue is False


def test_typed_evidence_goal_becomes_blocked_when_replan_budget_is_exhausted():
    ctx = _ctx()
    install_recovery_goal(ctx, {
        "goal": {
            "evidence_kind": "live_fact", "target": {"resource_id": "r1"},
            "fact": "status", "description": "observe live status",
        },
    }, source_call_id="source")
    unavailable = StreamingToolResult(
        tool_name="system.manage", call_id="read",
        output={"evidence_claims": [{
            "evidence_kind": "live_fact", "target": {"resource_id": "r1"},
            "fact": "status", "status": "unavailable",
        }]}, ok=False, error="unavailable",
    )
    assert recovery_final_gate(ctx, [unavailable], max_replans=1).should_continue is True
    assert recovery_final_gate(ctx, [unavailable], max_replans=1).should_continue is False
    assert ctx.extras["recovery_goals"][0]["status"] == "blocked"
    assert goal_loop_summary(ctx)["status"] == "blocked"


def test_query_loop_rejects_premature_final_and_accepts_corrected_web_read():
    responses = [
        LLMResponse(tool_calls=[LLMToolCall(
            id="bad-search", name="web.manage",
            arguments={"action": "search", "query": "bad syntax"},
        )]),
        LLMResponse(content="搜索失败，无法完成。"),
        LLMResponse(tool_calls=[LLMToolCall(
            id="corrected-search", name="web.manage",
            arguments={"action": "search", "query": "correct syntax"},
        )]),
        LLMResponse(content="已经通过修正后的检索获得所需证据。"),
    ]
    received: list[str] = []

    def llm(**_kwargs):
        return responses.pop(0)

    def web(arguments: dict):
        received.append(str(arguments["query"]))
        if arguments["query"] == "bad syntax":
            return {"ok": False, "error": "invalid provider query", "error_code": "ARGS_INVALID"}
        return {"ok": True, "results": [{"title": "evidence"}]}

    registry = {
        "web.manage": {
            "description": "web search",
            "args_schema": {
                "type": "object", "required": ["action", "query"],
                "properties": {
                    "action": {"type": "string", "enum": ["search"]},
                    "query": {"type": "string"},
                },
            },
        },
    }
    config = SSOTRuntimeConfig(max_query_loop_iterations=5)
    runtime = ToolRuntime(config)
    runtime.register("web.manage", web)
    outcome = asyncio.run(SSOTRuntimeEngine(
        config, llm_invoke=llm, tool_registry=registry, tool_runtime=runtime,
    ).run("搜索所需证据", workspace_id="test", session_id="session"))

    assert received == ["bad syntax", "correct syntax"]
    assert outcome.success is True
    assert outcome.metadata["goal_loop"]["status"] == "passed"
    assert any(
        item.get("type") == "premature_final_rejected"
        for item in outcome.metadata["recovery_goal_events"]
    )
