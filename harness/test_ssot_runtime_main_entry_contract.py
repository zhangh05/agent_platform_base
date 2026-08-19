"""SSOT Runtime main-entry contract tests."""


def test_default_output_budget_stays_bounded():
    from core.runtime_engine.models import SSOTRuntimeConfig

    assert SSOTRuntimeConfig().max_output_tokens == 8192


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


def test_agent_app_projects_single_terminal_cancel_error_to_task_state(monkeypatch, temp_dirs):
    """A QueryLoop primary error is a lifecycle fact even when errors is empty."""
    from types import SimpleNamespace
    from agent.app.facade import AgentApp

    class FakeEngine:
        async def run(self, **_kwargs):
            return SimpleNamespace(
                success=False,
                final_response="任务已取消。",
                node_results={},
                error="cancelled_by_user",
                errors=[],
                metadata={"execution_outcome": "failed"},
            )

    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._build_engine",
        lambda **_kwargs: FakeEngine(),
    )
    result = AgentApp().submit_user_message(
        user_input="生成答复",
        workspace_id="default",
        metadata={"transport": "test"},
    )
    assert result.ok is False
    assert result.errors == ["cancelled_by_user"]
    assert result.metadata["runtime_errors"] == ["cancelled_by_user"]
    assert result.metadata["task_state"]["task"]["status"] == "cancelled"
    assert result.metadata["task_state"]["task"]["next_action"] == "cancelled_by_user"


def test_agent_app_projects_unknown_outcome_as_read_only_terminal_fact(monkeypatch, temp_dirs):
    """The UI contract may observe uncertainty, but cannot own execution recovery."""
    from types import SimpleNamespace
    from agent.app.facade import AgentApp

    class FakeEngine:
        async def run(self, **_kwargs):
            return SimpleNamespace(
                success=False,
                final_response="外部写操作等待受控核对。",
                node_results={},
                errors=["unknown_outcome"],
                metadata={
                    "execution_outcome": "unknown",
                    "unknown_outcome": {
                        "status": "unknown",
                        "tool_id": "workspace.file",
                        "call_id": "call-write-contract",
                        "error_code": "TOOL_TIMEOUT_UNCERTAIN",
                        "execution_may_continue": True,
                    },
                    "goal_assertions": {"required": True, "status": "unknown"},
                },
            )

    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._build_engine",
        lambda **_kwargs: FakeEngine(),
    )
    result = AgentApp().submit_user_message(
        user_input="写入配置",
        workspace_id="default",
        metadata={"transport": "test"},
    )

    assert result.ok is False
    assert result.metadata["execution_outcome"] == "unknown"
    assert result.metadata["unknown_outcome"]["tool_id"] == "workspace.file"
    assert result.metadata["unknown_outcome"]["call_id"] == "call-write-contract"
    assert result.metadata["goal_assertions"]["status"] == "unknown"


def test_agent_app_ignores_malformed_optional_terminal_facts(monkeypatch, temp_dirs):
    """Optional metadata must not turn a completed request into a projection crash."""
    from types import SimpleNamespace
    from agent.app.facade import AgentApp

    class FakeEngine:
        async def run(self, **_kwargs):
            return SimpleNamespace(
                success=True,
                final_response="完成。",
                node_results={},
                errors=[],
                metadata={
                    "execution_outcome": "complete",
                    "unknown_outcome": "invalid",
                    "goal_assertions": ["invalid"],
                },
            )

    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._build_engine",
        lambda **_kwargs: FakeEngine(),
    )
    result = AgentApp().submit_user_message(
        user_input="检查状态",
        workspace_id="default",
        metadata={"transport": "test"},
    )

    assert result.ok is True
    assert result.metadata["unknown_outcome"] == {}
    assert result.metadata["goal_assertions"] == {}


def test_agent_app_projects_server_owned_cognitive_summary_and_events(monkeypatch, temp_dirs):
    """The SSOT adapter must retain QueryLoop's safe cognitive projection."""
    from types import SimpleNamespace
    from agent.app.facade import AgentApp

    cognitive = {
        "outcome": "stop_completed",
        "visible_summary": "目标、证据和安全条件已满足，可以生成最终结论。",
    }
    cognitive_events = [{
        "event_id": "cog-terminal",
        "type": "cognitive_stop_decided",
        "turn_id": "server-turn",
        "trace_id": "server-trace",
        "state_revision": 4,
        "payload": {},
    }]

    class FakeEngine:
        async def run(self, **_kwargs):
            return SimpleNamespace(
                success=True,
                final_response="完成。",
                node_results={},
                errors=[],
                metadata={
                    "execution_outcome": "complete",
                    "cognitive": cognitive,
                    "cognitive_events": cognitive_events,
                },
            )

    monkeypatch.setattr(
        "agent.runtime.ssot_runtime._build_engine",
        lambda **_kwargs: FakeEngine(),
    )
    result = AgentApp().submit_user_message(
        user_input="检查结果",
        workspace_id="default",
        # Request-side metadata must not own the server projection.
        metadata={"transport": "test", "cognitive": {"outcome": "forged"}},
    )

    assert result.metadata["cognitive"] == cognitive
    assert result.metadata["cognitive_events"] == cognitive_events
    assert result.metadata["cognitive"]["outcome"] != "forged"
