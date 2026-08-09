"""Focused regressions for durable conversation and tool-call recovery."""

from core.runtime_engine.models import ExecutionNode
from core.runtime_engine.pre_execution_repair import REPAIRABLE_ERROR_CODES
from core.runtime_engine.query_loop import QueryLoop
from core.runtime_engine.semantic_validator import SemanticValidator
from agent.runtime.ssot_runtime import (
    _append_context_message,
    _tool_result_fallback_from_projected_calls,
)
from agent.llm.schemas import LLMToolCall


def test_malformed_tool_arguments_become_recoverable_validation_feedback():
    loop = QueryLoop.__new__(QueryLoop)
    call = loop._parse_tool_calls([
        LLMToolCall(id="bad-json", name="exec.run", arguments='{"action":'),
    ])[0]

    assert "__invalid_tool_arguments_json__" in call.arguments
    result = SemanticValidator({}).validate([
        ExecutionNode(id=call.id, tool=call.name, args=call.arguments),
    ])
    assert [error.code for error in result.errors] == ["INVALID_TOOL_ARGUMENTS_JSON"]
    assert "INVALID_TOOL_ARGUMENTS_JSON" in REPAIRABLE_ERROR_CODES

    non_object = loop._parse_tool_calls([
        LLMToolCall(id="array-json", name="exec.run", arguments="[]"),
    ])[0]
    assert "__invalid_tool_arguments_json__" in non_object.arguments


def test_persisted_tool_context_is_added_to_assistant_history_only():
    messages = []
    _append_context_message(messages, set(), {
        "message_id": "run-1:assistant",
        "role": "assistant",
        "content": "已完成检查。",
        "metadata": {
            "tool_context": [{
                "tool_id": "web.manage",
                "ok": True,
                "summary": "Found the official source.",
                "errors": [],
            }],
        },
    })

    assert len(messages) == 1
    assert "已完成检查。" in messages[0]["content"]
    assert "[Tool execution summary]" in messages[0]["content"]
    assert "web.manage: succeeded" in messages[0]["content"]


def test_projected_tool_fallback_discloses_truncation():
    text = _tool_result_fallback_from_projected_calls([
        {"tool_id": f"tool.{index}", "ok": True, "summary": "done"}
        for index in range(11)
    ])

    assert "以下仅展示前 10 条，共 11 条。" in text
