import pytest

from agent.runtime.task_relation_policy import classify_task_relation


@pytest.mark.parametrize(
    ("prompt", "kind"),
    [
        ("再来30条", "append"),
        ("继续", "expand"),
        ("把第三部分重写得更正式", "rewrite"),
        ("删除风险章节，只保留实施步骤", "scope"),
        ("补齐缺失的回滚条件", "repair"),
        ("总结并收敛为一页", "summarize"),
        ("优化表达，保持事实不变", "refine"),
    ],
)
def test_classifies_explicit_continuation_operations(prompt, kind):
    relation = classify_task_relation(prompt)
    assert relation is not None
    assert relation["kind"] == kind
    if kind not in {"append", "expand"}:
        assert relation["instruction_present"] is True


def test_append_preserves_mechanical_count_and_unit():
    assert classify_task_relation("再来30条") == {
        "kind": "append", "expected_new_items": 30, "unit": "条"
    }


def test_new_topic_is_not_classified_as_a_continuation_operation():
    assert classify_task_relation("分析杭州未来三天天气") is None


def test_oversized_instruction_is_not_promoted_to_a_contract_relation():
    assert classify_task_relation("重写" + "内容" * 121) is None
