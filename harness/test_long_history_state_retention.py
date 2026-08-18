"""Long-session state-retention regressions."""


def test_unresolved_tool_state_survives_summary_priority_pressure():
    from agent.runtime.ssot_runtime import _summarize_older_messages

    messages = [
        {
            "role": "assistant",
            "content": f"必须遵守无关约束 {index}。",
            "history_state": {"signals": ["constraint"]},
        }
        for index in range(13)
    ]
    messages.insert(4, {
        "role": "assistant",
        "content": "等待后续安排。",
        "history_state": {
            "signals": [],
            "tool_facts": [{"tool_id": "web.manage", "ok": False, "summary": "provider unavailable"}],
            "unresolved": [{"tool_id": "web.manage", "ok": False, "summary": "provider unavailable"}],
        },
    })

    summary = _summarize_older_messages(messages, max_tokens=4000)

    assert "provider unavailable" in summary
