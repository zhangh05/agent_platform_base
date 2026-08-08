"""Web search resilience and user-facing fallback output."""

from __future__ import annotations


def test_web_manage_search_degrades_to_official_candidate_when_search_providers_fail(monkeypatch):
    import sys
    import types

    import core.tools.general_tools.web_tools as web_tools
    from core.tools.schemas import ToolInvocation

    class FailingRequests:
        @staticmethod
        def get(*args, **kwargs):
            raise TimeoutError("provider timed out")

    class FailingDDGS:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def text(self, *args, **kwargs):
            raise TimeoutError("ddgs timed out")

    requests_module = types.ModuleType("requests")
    requests_module.get = FailingRequests.get
    ddgs_module = types.ModuleType("ddgs")
    ddgs_module.DDGS = FailingDDGS
    monkeypatch.setitem(sys.modules, "requests", requests_module)
    monkeypatch.setitem(sys.modules, "ddgs", ddgs_module)

    result = web_tools.handle_web_search(ToolInvocation(
        tool_id="web.manage",
        arguments={"action": "search", "query": "什么是 K8s Kubernetes 简介", "max_results": 3},
        workspace_id="default",
        requested_by="turn_runner",
    ))

    assert result["ok"] is True
    assert result["status"] == "partial"
    assert result["provider"] == "curated_official_fallback"
    assert result["results"][0]["url"] == "https://kubernetes.io/docs/"
    assert "搜索服务暂时不可用" in result["summary"]


def test_web_tool_fallback_is_human_readable():
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.query_loop import QueryLoop, StreamingToolResult

    loop = QueryLoop(SSOTRuntimeConfig(), {}, None)
    text = loop._build_tool_result_fallback(None, [
        StreamingToolResult(
            tool_name="web.manage",
            call_id="call_1",
            ok=True,
            output={
                "ok": True,
                "status": "partial",
                "provider": "curated_official_fallback",
                "summary": "搜索服务暂时不可用；已返回 1 个官方来源候选",
                "results": [{
                    "title": "Kubernetes 官方文档",
                    "url": "https://kubernetes.io/docs/",
                    "snippet": "Kubernetes 官方文档入口",
                }],
                "answer_hint": "搜索引擎 provider 暂时不可用；以下是内置官方来源候选。",
            },
        )
    ])

    assert "联网结果" in text
    assert "官方来源候选" in text
    assert "Kubernetes 官方文档" in text
    assert "工具调用：成功" not in text
    assert "web_search_provider_error" not in text
