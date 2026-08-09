"""End-to-end contracts for base-platform and specialist prompt behavior."""

from types import SimpleNamespace


def _rich_context() -> dict:
    return {
        "intent": "workspace_review",
        "result": {"status": "ok", "summary": "completed"},
        "last_result_summary": "completed with one warning",
        "job_summary": {"status": "partial", "failed": 1},
        "stats": {
            "total_lines": 30,
            "meaningful_lines": 20,
            "coverage_pct": 90,
            "usable_items": 18,
            "exact_match_count": 10,
            "typed_ir_count": 4,
            "typed_ir_exact": 3,
            "typed_ir_semantic": 1,
            "pattern_match_count": 4,
            "passthrough_count": 0,
            "manual_review_count": 2,
            "semantic_near_count": 1,
            "unsupported_count": 1,
        },
        "quality_summary": {
            "source_residue_count": 0,
            "silent_drop_count": 0,
            "safe_drop_count": 2,
            "review_required_count": 2,
        },
        "top_review_items": [{
            "severity": "high",
            "line_number": 8,
            "source_line": "sample field",
            "reason": "mapping needs review",
            "confidence": 0.6,
            "suggested_action": "verify field mapping",
        }],
        "artifact_refs": [{
            "artifact_id": "art_1",
            "artifact_type": "report",
            "summary": "safe report summary",
        }],
        "memory_hits": [{"title": "workspace", "summary": "verified fact"}],
        "knowledge_hits": [{
            "artifact_id": "art_k1",
            "chunk_id": "chunk_1",
            "title": "Operations guide",
            "artifact_type": "knowledge",
            "sensitivity": "internal",
            "score": 0.9,
            "summary": "Operational review guidance",
            "safe_excerpt": "Check source evidence and timestamp consistency.",
        }],
    }


def test_every_enabled_template_renders_without_template_syntax():
    from prompts.loader import load_prompt_registry, render_prompt

    context = _rich_context()
    citations = [{
        "citation_id": "K1",
        "source_type": "knowledge",
        "source_id": "art_k1",
    }]
    for spec in load_prompt_registry():
        if spec.status != "enabled":
            continue
        rendered = render_prompt(
            spec.task,
            safe_context=context,
            user_input="请给出结论",
            citations=citations,
        )
        assert "{{" not in rendered.text, spec.prompt_id
        assert "{%" not in rendered.text, spec.prompt_id


def test_knowledge_prompt_renders_real_values():
    from prompts.loader import render_prompt

    context = _rich_context()
    knowledge = render_prompt("knowledge_answer", context, "怎么检查？")

    assert "Operations guide" in knowledge.text
    assert "Check source evidence and timestamp consistency." in knowledge.text


def test_specialist_prompts_have_distinct_operational_contracts():
    from prompts.loader import render_prompt

    context = _rich_context()
    prompts = {
        task: render_prompt(task, context, "分析结果").text
        for task in (
            "response_compose",
            "context_qa",
            "knowledge_answer",
            "artifact_summary_explain",
            "job_failure_explain",
            "manual_review_explain",
            "report_summary",
            "result_summarize",
            "memory_consolidation",
        )
    }

    assert "pending, running, partial" in prompts["response_compose"]
    assert "historical result" in prompts["context_qa"]
    assert "supplied sources conflict" in prompts["knowledge_answer"]
    assert "provenance, scope" in prompts["artifact_summary_explain"]
    assert "Retry eligibility or blocker" in prompts["job_failure_explain"]
    assert "smallest concrete check" in prompts["manual_review_explain"]
    assert "observation time" in prompts["report_summary"]
    assert "tool's success" in prompts["result_summarize"]
    assert "Raw device state" in prompts["memory_consolidation"]
    assert "core_rule" in prompts["memory_consolidation"]


def test_enabled_prompt_registry_is_latest_only():
    from prompts.loader import load_prompt_registry

    for spec in load_prompt_registry():
        assert spec.version in {"v2", "v3"}
        assert not spec.prompt_id.endswith(".v1")


def test_registry_context_limits_are_applied_before_rendering():
    from prompts.loader import render_prompt

    hits = []
    for index in range(20):
        hits.append({
            "artifact_id": f"art_{index}",
            "chunk_id": f"chunk_{index}",
            "title": f"title-{index}",
            "artifact_type": "knowledge",
            "sensitivity": "internal",
            "score": 1,
            "summary": "s" * 2000,
            "safe_excerpt": "e" * 2000,
        })
    rendered = render_prompt(
        "knowledge_answer",
        {"knowledge_hits": hits},
        "question",
    )

    assert rendered.context_chars <= 6000
    assert "knowledge_hits_truncated:20->8" in rendered.warnings
    assert any(warning.startswith("context_truncated_to:") for warning in rendered.warnings)


def test_extra_context_cannot_bypass_registry_budget():
    from prompts.loader import render_prompt

    rendered = render_prompt(
        "response_compose",
        safe_context={},
        user_input="review",
        extra={
            "stats": {"total_lines": 10, "coverage_pct": 80},
            "top_review_items": [
                {
                    "severity": "high",
                    "line_number": index,
                    "source_line": "x" * 4000,
                    "reason": "r" * 4000,
                    "confidence": 0.5,
                    "suggested_action": "a" * 4000,
                }
                for index in range(30)
            ],
        },
    )

    assert rendered.context_chars <= 8000
    assert any(warning.startswith("context_truncated_to:") for warning in rendered.warnings)


def test_tool_result_keeps_runtime_contract_with_response_nudge_marker():
    from agent.llm.schemas import LLMMessage
    from core.runtime_engine.prompt_contract import RUNTIME_SYSTEM_PROMPT
    from core.runtime_engine.query_loop import (
        RESPONSE_ONLY_MARKER,
        QUERY_LOOP_SYSTEM_PROMPT,
        QueryLoop,
    )

    ctx = SimpleNamespace(extras={})
    continuation = [
        LLMMessage(role="system", content="system"),
        LLMMessage(role="tool", content="first result"),
    ]
    prompt, scope, streams = QueryLoop._llm_call_mode(continuation, ctx)
    assert prompt == RUNTIME_SYSTEM_PROMPT
    assert scope == "continuation"
    assert streams is True

    response = continuation + [
        LLMMessage(role="user", content=RESPONSE_ONLY_MARKER),
    ]
    prompt, scope, streams = QueryLoop._llm_call_mode(response, ctx)
    assert prompt == QUERY_LOOP_SYSTEM_PROMPT
    assert scope == "response"
    assert streams is True


def test_unknown_prompt_task_fails_without_generic_assistant_fallback(monkeypatch):
    from agent.llm.runtime import invoke_llm, safe_generate

    monkeypatch.setattr("agent.llm.config.resolve_provider_config", lambda: {
        "enabled": True,
        "provider_type": "mock",
    })
    direct = invoke_llm(task="missing_prompt_task")
    safe = safe_generate(task="missing_prompt_task")

    assert direct.error == "prompt_runtime_error:PromptNotFoundError"
    assert direct.metadata["retryable"] is False
    assert safe.llm_used is False
    assert safe.fallback_reason == "prompt_runtime_error"
    assert safe.metadata["provider_called"] is False
