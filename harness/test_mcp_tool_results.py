"""MCP protocol-level failures must not become successful tool calls."""

from types import SimpleNamespace

from core.tools.general_tools.skill_tools import handle_mcp_call
from core.tools.schemas import ToolInvocation


def test_mcp_is_error_result_is_reported_as_tool_failure(monkeypatch):
    provider = SimpleNamespace(
        provider_id="test", provider_type="mcp", status="enabled",
        trust_level="local", command=("unused",), root_path="", permissions=[],
        tools=[{"name": "inspect", "enabled": True}],
    )
    monkeypatch.setattr("core.tools.general_tools.skill_tools._mcp_provider", lambda _inv: provider)

    class _Client:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def call_tool(self, *_args, **_kwargs):
            return {"isError": True, "content": [{"type": "text", "text": "device unavailable"}]}

    monkeypatch.setattr("core.tools.mcp_client.StdioMcpClient", _Client)
    result = handle_mcp_call(ToolInvocation(
        tool_id="skill.manage", workspace_id="ws_mcp", 
        arguments={"action": "mcp_call", "provider_id": "test", "tool_name": "inspect"},
    ))
    assert result["ok"] is False
    assert "device unavailable" in result["error"]
