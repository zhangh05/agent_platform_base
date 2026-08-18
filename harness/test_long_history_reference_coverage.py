"""Long-session explicit-reference regressions for conversation context."""


def test_chinese_explicit_reference_retrieves_relevant_middle_history():
    from agent.runtime.ssot_runtime import _build_history_block
    from types import SimpleNamespace

    messages = []
    for index in range(40):
        content = f"常规讨论第{index}条"
        if index == 18:
            content = "蓝杉茶与月白茶的顺序是先蓝杉后月白。"
        messages.append({
            "message_id": f"m-{index}",
            "role": "assistant" if index % 2 else "user",
            "content": content,
        })

    class Store:
        def __init__(self, **_kwargs):
            pass
        def get_messages(self):
            return messages

    import storage.message_store
    original = storage.message_store.SessionMessageStore
    storage.message_store.SessionMessageStore = Store
    try:
        session = SimpleNamespace(workspace_id="ws", session_id="s", history=[])
        block = _build_history_block(
            session,
            user_input="之前蓝杉茶的顺序是什么？",
            max_tokens=1600,
        )
    finally:
        storage.message_store.SessionMessageStore = original

    assert "蓝杉茶与月白茶的顺序" in block
