from __future__ import annotations

from types import SimpleNamespace

from agent.runtime.ssot_runtime import (
    _build_history_block,
    _recent_session_attachments,
    _select_history_messages,
)


def _message(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def test_new_topic_still_receives_immediately_previous_exchange():
    messages = [
        _message("user", "分析交换机配置"),
        _message("assistant", "这是设备接口、VRRP 与 OSPF 的详细分析。"),
    ]

    recent, older, retrieved = _select_history_messages(
        messages,
        "查看未来十天长三角天气",
    )

    assert [item["content"] for item in recent] == [
        "分析交换机配置",
        "这是设备接口、VRRP 与 OSPF 的详细分析。",
    ]
    assert older == []
    assert retrieved is False


def test_all_scope_followup_keeps_only_immediate_exchange():
    messages = [
        _message("user", "分析交换机配置"),
        _message("assistant", "很长的设备配置分析"),
        _message("user", "查看未来十天长三角天气"),
        _message("assistant", "希望查询哪个城市，或者几个主要城市？"),
    ]

    recent, older, retrieved = _select_history_messages(messages, "全部")

    assert [item["content"] for item in recent] == [
        "查看未来十天长三角天气",
        "希望查询哪个城市，或者几个主要城市？",
    ]
    assert older == []
    assert retrieved is False


def test_standalone_all_scope_request_is_not_mistaken_for_bare_followup():
    messages = [
        _message("user", "查询杭州天气"),
        _message("assistant", "杭州今天有阵雨。"),
    ]

    recent, older, retrieved = _select_history_messages(
        messages,
        "检查所有工具的描述是否准确",
    )

    assert [item["content"] for item in recent] == [
        "查询杭州天气",
        "杭州今天有阵雨。",
    ]
    assert older == []
    assert retrieved is False


def test_standalone_related_request_can_reuse_matching_exchange():
    messages = [
        _message("user", "查询杭州天气"),
        _message("assistant", "杭州今天有阵雨。"),
        _message("user", "解释交换机 OSPF 配置"),
        _message("assistant", "OSPF area 1 已配置。"),
    ]

    recent, _, _ = _select_history_messages(messages, "杭州天气未来趋势")

    assert any("杭州" in item["content"] for item in recent)
    assert any("OSPF" in item["content"] for item in recent)


def test_short_repair_and_urgency_turns_never_receive_empty_history():
    messages = [
        _message("user", "写一篇不少于800字的作文"),
        _message("assistant", "当前正文不足800字。"),
    ]

    for followup in ("快点", "补充", "补充啊"):
        recent, older, retrieved = _select_history_messages(messages, followup)
        assert [item["content"] for item in recent] == [
            "写一篇不少于800字的作文",
            "当前正文不足800字。",
        ]
        assert older == []
        assert retrieved is False


def test_historical_attachment_is_not_injected_after_topic_has_moved(monkeypatch):
    messages = [
        {"role": "user", "metadata": {"attachments": [{"file_id": "file_config"}]}},
        {"role": "assistant", "content": "配置分析完成"},
        {"role": "user", "content": "查询天气"},
        {"role": "assistant", "content": "想查哪个城市"},
    ]

    class Store:
        def __init__(self, **_kwargs):
            pass

        def get_messages(self):
            return messages

    monkeypatch.setattr("storage.message_store.SessionMessageStore", Store)
    session = SimpleNamespace(workspace_id="default", session_id="s1")

    assert _recent_session_attachments(session, user_input="全部") == []


def test_historical_attachment_is_reused_for_immediate_followup(monkeypatch):
    messages = [
        {"role": "user", "metadata": {"attachments": [{"file_id": "file_config"}]}},
        {"role": "assistant", "content": "配置分析完成"},
    ]

    class Store:
        def __init__(self, **_kwargs):
            pass

        def get_messages(self):
            return messages

    monkeypatch.setattr("storage.message_store.SessionMessageStore", Store)
    session = SimpleNamespace(workspace_id="default", session_id="s1")

    assert _recent_session_attachments(session, user_input="再详细一点") == [
        {"file_id": "file_config"},
    ]


def test_quantity_only_continuation_keeps_immediate_exchange_constraints():
    messages = [
        _message("user", "连续输出24条企业网络值班检查项；每条完整中文，使用编号，不调用工具。"),
        _message("assistant", "1. 核对交接班日志。\n2. 检查核心链路。"),
        _message("user", "查询杭州天气"),
        _message("assistant", "杭州今天有阵雨。"),
        _message("user", "连续输出24条企业网络值班检查项；每条完整中文，使用编号，不调用工具。"),
        _message("assistant", "1. 检查监控平台。\n2. 核对告警状态。"),
    ]

    recent, older, retrieved = _select_history_messages(messages, "再来30条")

    assert [item["content"] for item in recent] == [
        "连续输出24条企业网络值班检查项；每条完整中文，使用编号，不调用工具。",
        "1. 检查监控平台。\n2. 核对告警状态。",
    ]
    assert older == []
    assert retrieved is False


def test_quantity_only_continuation_projects_constraints_into_prompt_history(monkeypatch):
    messages = [
        _message("user", "连续输出24条企业网络值班检查项；每条完整中文，使用编号，不调用工具。"),
        _message("assistant", "1. 检查监控平台。\n2. 核对告警状态。"),
    ]
    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._load_context_messages",
        lambda *_args, **_kwargs: messages,
    )

    block = _build_history_block(
        SimpleNamespace(),
        user_input="再来30条",
    )

    assert "RECENT CONVERSATION HISTORY:" in block
    assert "连续输出24条企业网络值班检查项" in block
    assert "每条完整中文，使用编号，不调用工具" in block
    assert "检查监控平台" in block


def test_short_repair_projects_previous_exchange_into_prompt_history(monkeypatch):
    messages = [
        _message("user", "写一篇不少于800字的作文"),
        _message("assistant", "当前版本只有642字，需要继续补足。"),
    ]
    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._load_context_messages",
        lambda *_args, **_kwargs: messages,
    )

    block = _build_history_block(SimpleNamespace(), user_input="补充")

    assert "写一篇不少于800字的作文" in block
    assert "当前版本只有642字，需要继续补足" in block
