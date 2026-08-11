from __future__ import annotations

from types import SimpleNamespace

from agent.runtime.ssot_runtime import (
    _recent_session_attachments,
    _select_history_messages,
)


def _message(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def test_new_topic_does_not_inherit_unrelated_large_history():
    messages = [
        _message("user", "分析交换机配置"),
        _message("assistant", "这是设备接口、VRRP 与 OSPF 的详细分析。"),
    ]

    recent, older, retrieved = _select_history_messages(
        messages,
        "查看未来十天长三角天气",
    )

    assert recent == []
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

    assert recent == []
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
    assert all("OSPF" not in item["content"] for item in recent)


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
