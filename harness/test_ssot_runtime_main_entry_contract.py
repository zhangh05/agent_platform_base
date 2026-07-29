"""SSOT Runtime main-entry contract tests."""


def test_agent_app_submit_uses_ssot_runtime(monkeypatch, temp_dirs):
    from agent.app.facade import AgentApp

    def fake_llm(**kwargs):
        system = str(kwargs.get("system") or "")
        if "execution planner" in system.lower():
            return '{"nodes":[],"final_response":"收到"}'
        return "收到"

    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._invoke_llm_for_ssot_runtime",
        fake_llm,
    )

    result = AgentApp().submit_user_message(
        user_input="你好",
        workspace_id="default",
        metadata={"transport": "test"},
    )

    assert result.ok is True
    assert result.final_response.strip("。") == "收到"
    assert result.metadata["runtime_engine"] == "ssot_runtime"
    assert result.metadata["timeline_summary"]["llm_calls"] == 1
    assert result.tool_calls == []
