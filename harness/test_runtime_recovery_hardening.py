"""Focused regressions for durable conversation and tool-call recovery."""

import asyncio

from core.runtime_engine.models import ExecutionNode
from core.runtime_engine.models import SSOTRuntimeConfig, StatelessContext
from core.runtime_engine.pre_execution_repair import REPAIRABLE_ERROR_CODES
from core.runtime_engine.query_loop import QueryLoop
from core.runtime_engine.query_loop import _llm_failure_message, _normalize_llm_error
from core.runtime_engine.semantic_validator import SemanticValidator
from agent.runtime.ssot_runtime import (
    _append_context_message,
    _tool_result_fallback_from_projected_calls,
)
from agent.llm.schemas import LLMMessage, LLMToolCall
from agent.runtime.turn_persistence import _history_tool_context


def test_non_web_tool_fallback_never_exposes_internal_tool_transcript():
    from core.runtime_engine.query_loop import StreamingToolResult

    loop = QueryLoop(SSOTRuntimeConfig(), {}, None)
    text = loop._build_tool_result_fallback(None, [StreamingToolResult(
        tool_name="workspace.file", call_id="call-1", ok=False, output={},
        error="file not found: private-image.png",
    )])

    assert "private-image.png" not in text
    assert "workspace.file" not in text
    assert "可靠答复" in text


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


def test_llm_errors_are_normalized_before_reaching_user_projections():
    assert _normalize_llm_error("HTTP Error 429: provider says too many requests") == "llm_rate_limited"
    assert _normalize_llm_error("KeyError: choices") == "llm_provider_error"
    assert "KeyError" not in _llm_failure_message("llm_provider_error")
    assert "繁忙" in _llm_failure_message("llm_rate_limited")


def test_llm_invocation_never_returns_raw_exception_text():
    def broken_provider(**_kwargs):
        raise KeyError("choices")

    loop = QueryLoop(SSOTRuntimeConfig(), {}, None, llm_invoke=broken_provider)
    response = asyncio.run(loop._call_llm(
        [LLMMessage(role="user", content="hello")],
        StatelessContext(workspace_id="default", session_id="s1", request_id="r1", user_input="hello"),
    ))

    assert response.error == "llm_provider_error"


def test_history_tool_context_keeps_failures_and_latest_evidence():
    result = type("Result", (), {"tool_calls": [
        {
            "call_id": f"call-{index}", "tool_id": f"tool.{index}",
            "ok": index != 5, "summary": f"result {index}", "errors": ["failed"] if index == 5 else [],
        }
        for index in range(12)
    ]})()

    context = _history_tool_context(result)
    assert len(context) == 8
    assert any(item["tool_id"] == "tool.5" and not item["ok"] for item in context)
    assert context[-1]["tool_id"] == "tool.11"


def test_secret_artifact_is_redacted_at_rest_and_not_readable(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from artifacts.store import read_artifact_content, save_artifact

    artifact = save_artifact(
        "default", "password=top-secret", sensitivity="secret",
        artifact_type="output_data", metadata={"api_key": "top-secret"},
    )
    assert artifact is not None
    assert artifact.metadata["api_key"] == "[REDACTED_SECRET]"
    assert read_artifact_content("default", artifact.artifact_id, allow_sensitive=True) is None
