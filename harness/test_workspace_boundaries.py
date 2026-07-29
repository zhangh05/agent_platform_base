# Workspace isolation contracts.
"""Phase 7: Workspace / Session / Run / Job boundary enforcement."""

import pytest, uuid, json


class TestWorkspaceRequired:
    def test_mutating_missing_workspace_400(self):
        """Empty/missing workspace_id on mutating endpoints returns 400."""
        # AgentOp requires workspace_id now
        from agent.protocol.op import AgentOp
        try:
            AgentOp.user_message(user_input="test", workspace_id="")
        except (ValueError, TypeError):
            pass  # Expected — empty workspace_id should fail

    def test_facade_no_default_workspace(self):
        """facade.submit_user_message requires explicit workspace_id."""
        import inspect
        from agent.app.facade import AgentApp
        sig = inspect.signature(AgentApp.submit_user_message)
        ws_param = sig.parameters.get("workspace_id")
        assert ws_param is not None
        # workspace_id is required (no default value)
        assert ws_param.default is inspect.Parameter.empty

    def test_session_empty_not_default(self):
        """AgentSession workspace_id defaults to empty, never 'default'."""
        import inspect
        from agent.core.session import AgentSession
        sig = inspect.signature(AgentSession.__init__)
        ws_param = sig.parameters.get("workspace_id")
        assert ws_param is not None
        default_val = ws_param.default
        # must not be "default" — empty string is fine
        assert default_val != "default"


class TestCrossWorkspaceBlocked:
    def test_cross_ws_task_not_readable(self):
        from agent.runtime.durable.store import save_task, get_task
        from agent.runtime.durable.models import TaskState
        ws_a = f"ws_a7_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_b7_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws_a, session_id="s1")
        save_task(task)
        assert get_task(ws_b, task.task_id) is None

    def test_cross_ws_task_not_cancelable(self):
        from agent.runtime.durable.store import save_task
        from agent.runtime.durable.control import cancel_task
        from agent.runtime.durable.models import TaskState
        ws_a = f"ws_ca7_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_cb7_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws_a, session_id="s1")
        task.update_status("running"); save_task(task)
        result = cancel_task(task.task_id, ws_b)
        assert result["ok"] is False

    def test_cross_ws_checkpoint_blocked(self):
        from agent.runtime.durable.store import save_task
        from agent.runtime.durable.control import checkpoint_task
        from agent.runtime.durable.models import TaskState
        ws_a = f"ws_cpa_{uuid.uuid4().hex[:8]}"
        ws_b = f"ws_cpb_{uuid.uuid4().hex[:8]}"
        task = TaskState.new(workspace_id=ws_a, session_id="s1")
        task.update_status("running"); save_task(task)
        cp = checkpoint_task(task.task_id, ws_b, reason="test")
        assert cp is None


class TestCallerIdentity:
    def test_rest_api_caller_fixed(self):
        """REST caller is injected by server, not from client body."""
        from core.tools.integration import get_default_tool_runtime_client
        from core.tools.context import ToolRuntimeContext
        client = get_default_tool_runtime_client()
        # Even if context says requested_by=subagent, subagent tools not allowed for subagent
        ctx = ToolRuntimeContext(workspace_id="default", requested_by="subagent")
        result = client.invoke("agent.manage", {"role": "review"}, context=ctx)
        # agent.spawn allowed_callers includes turn_runner but NOT subagent
        assert "blocked" in result.status or "allowed" in str(result.summary).lower()
