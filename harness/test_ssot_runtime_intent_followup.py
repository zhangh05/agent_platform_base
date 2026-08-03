"""
``detect_task_intent`` must NOT classify
meta-questions about past behaviour as task-intent.

Background: v4 added ``EXECUTION_OBLIGATION_ENFORCED`` which
raises ``ExecutionObligationViolation`` when the planner returns
an empty graph for a task-intent request. The LLM correctly
returns an empty plan for "你上轮为什么不总结" because no tool
is needed — but the intent detector matched "总结" as a task
verb and flagged it as a task, so the obligation guard fired
and the engine returned a structured error.

The fix: ``detect_task_intent`` now has a Step 0 check that
matches meta-questions (为什么/怎么/是不是/...) combined with
past references (上轮/刚才/之前/...) and returns
``intent_type="conversational_followup"`` with
``is_task=False`` and ``requires_tool_likely=False``.

These tests cover the regression matrix:

  * The exact production bug case ("你上轮为什么不总结").
  * Other meta-question + past-reference combinations.
  * Real tasks with past references ("上次的报告给我") — these
    still go through the normal task-verb path, NOT the
    conversational-followup path, because they don't include a
    meta-question verb.
  * Real tasks with meta-questions about an artifact
    ("这个截图为什么这样") — also not affected; the past-ref
    signal is missing.
  * ``TaskIntentResult.requires_execution`` returns False for
    ``conversational_followup`` even if ``requires_tool_likely``
    is True (defence in depth).
"""

from __future__ import annotations

import pytest

from core.runtime_engine.engine import (
    TaskIntentResult,
    detect_task_intent,
)
from core.runtime_engine.fast_path import is_conversation_comprehension_ref


# ── A: the exact production bug case ────────────────────────────────


def test_audit_bug_you_did_not_summarize_last_turn():
    """Reproduces the audit screenshot bug. The user asked
    "你上轮为什么不总结" — a meta-question about past behaviour.
    The detector must return is_task=False.
    """
    r = detect_task_intent("你上轮为什么不总结")
    assert r.is_task is False, (
        f"v4.3 regression: meta-question about past behaviour was "
        f"classified as task (intent={r.intent_type!r}, "
        f"evidence={r.evidence}). This is the production bug."
    )
    assert r.intent_type == "conversational_followup"
    assert r.requires_tool_likely is False
    assert r.requires_execution is False


# ── B: other meta-question + past-reference combinations ────────────


@pytest.mark.parametrize("query", [
    "上轮为什么不总结",
    "你上轮为什么不分析",
    "你上轮为什么不检查",
    "刚才怎么没分析",
    "你刚才为什么报错",
    "你上轮是不是有 bug",
    "上轮怎么没诊断",
    "你刚才是什么意思",
    "之前怎么没看到",
    "上一轮为什么没结果",
    "前一轮怎么样",
])
def test_meta_question_about_past_is_not_task(query: str):
    r = detect_task_intent(query)
    assert r.is_task is False, (
        f"meta-question {query!r} should NOT be task "
        f"(got is_task=True, intent={r.intent_type!r})"
    )
    assert r.intent_type == "conversational_followup"


# ── C: past reference without meta-question — NOT exempt ───────────


@pytest.mark.parametrize("query", [
    "上次的报告给我",
    "上轮的报告打开",
    "之前的报告呢",
    "把上次的报告发我",
])
def test_past_ref_alone_does_not_trigger_followup(query: str):
    """A request involving past content but without a meta-
    question verb must NOT be classified as a followup. The
    intent is ambiguous (could be a new task referencing past
    work, or just a chitchat) — we let the planner / LLM
    decide rather than blanket-classifying.
    """
    r = detect_task_intent(query)
    # The detector may return either is_task=True or False for
    # these inputs depending on what verbs/patterns are present.
    # The only invariant: it must NOT be
    # ``conversational_followup`` (that's reserved for the
    # meta-question case).
    assert r.intent_type != "conversational_followup", (
        f"query {query!r} has past-ref but no meta-question; "
        f"should not be classified as conversational_followup"
    )


# ── D: meta-question without past reference — NOT exempt ───────────


@pytest.mark.parametrize("query", [
    "为什么",
    "为什么这样",
    "这个截图为什么这样",
    "看看为什么有问题",
])
def test_meta_question_alone_is_still_task(query: str):
    """A meta-question without a past reference is still a
    real task (the user is asking about an artifact or current
    state). The "这个截图为什么这样" pattern is the canonical
    example — it's task, not a followup.
    """
    r = detect_task_intent(query)
    # The first two (bare "为什么", "为什么这样") have no verb
    # match and no past-ref → is_task=False. The latter two
    # match the contextual_inquiry rule → is_task=True.
    # We only assert: must NOT be conversational_followup.
    assert r.intent_type != "conversational_followup", (
        f"query {query!r} has no past-ref; should not be "
        f"classified as conversational_followup"
    )


# ── E: real tasks still classified as task ────────────────────────


@pytest.mark.parametrize("query", [
    "分析这份数据",
    "分析数据",
    "看看这个问题",
    "检查一下工作区文件",
    "导出分析结果",
    "帮我看看配置文件",
])
def test_real_tasks_still_classified_as_task(query: str):
    r = detect_task_intent(query)
    assert r.is_task is True, (
        f"real task {query!r} should be classified as task "
        f"(got is_task=False, intent={r.intent_type!r})"
    )
    assert r.intent_type != "conversational_followup"


# ── F: chitchat is still not task ─────────────────────────────────


@pytest.mark.parametrize("query", [
    "你好",
    "hello",
    "hi",
])
def test_chitchat_still_not_task(query: str):
    r = detect_task_intent(query)
    assert r.is_task is False
    assert r.intent_type != "conversational_followup"


@pytest.mark.parametrize("query", ["不可能啊", "不对", "你确定吗"])
def test_short_disagreement_uses_conversation_history(query: str):
    assert is_conversation_comprehension_ref(query) is True


def test_direct_answer_prompt_preserves_platform_capabilities():
    from core.runtime_engine.prompt_contract import DIRECT_ANSWER_PROMPT

    assert "Do not deny Agent Platform Base capabilities" in DIRECT_ANSWER_PROMPT
    assert "Prior assistant messages may summarize real" in DIRECT_ANSWER_PROMPT


# ── G: TaskIntentResult.requires_execution defence-in-depth ──────


def test_requires_execution_false_for_conversational_followup():
    """Even if a subclass or future detector sets
    ``requires_tool_likely=True`` for a conversational_followup,
    the ``requires_execution`` property must still return False.
    The v4 obligation guard reads this property, so the
    defence-in-depth here protects against future regressions.
    """
    r = TaskIntentResult(
        is_task=False,
        intent_type="conversational_followup",
        requires_tool_likely=True,  # intentionally wrong
    )
    assert r.requires_execution is False


def test_requires_execution_true_for_real_task():
    r = TaskIntentResult(
        is_task=True,
        intent_type="analysis",
        requires_tool_likely=True,
    )
    assert r.requires_execution is True
