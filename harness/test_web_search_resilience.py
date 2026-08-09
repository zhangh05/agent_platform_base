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


def test_authority_profile_routes_known_technical_scenes_to_primary_domains():
    from core.tools.general_tools.shared_web import _resolve_search_authority

    h3c = _resolve_search_authority({"authority_profile": "auto"}, "H3C 交换机版本说明")
    assert h3c["profile"] == "network_vendor"
    assert h3c["domains"] == ["h3c.com"]

    protocol = _resolve_search_authority({"authority_profile": "auto"}, "RFC 4271 BGP 状态机")
    assert protocol["profile"] == "protocol_standard"
    assert "rfc-editor.org" in protocol["domains"]

    vendor_protocol = _resolve_search_authority({"authority_profile": "auto"}, "H3C BGP 配置")
    assert vendor_protocol["profile"] == "network_vendor"
    assert vendor_protocol["domains"] == ["h3c.com"]

    security = _resolve_search_authority({"authority_profile": "auto"}, "CVE-2026-1234 受影响版本")
    assert security["profile"] == "security_advisory"
    assert security["domains"][:3] == ["cisa.gov", "nvd.nist.gov", "cve.org"]


def test_explicit_domains_override_automatic_authority_domains():
    from core.tools.general_tools.shared_web import _resolve_search_authority

    policy = _resolve_search_authority(
        {"authority_profile": "security_advisory", "allowed_domains": ["security.example.com"]},
        "CVE-2026-1234",
    )
    assert policy["profile"] == "security_advisory"
    assert policy["domains"] == ["security.example.com"]
    assert policy["explicit_domains"] is True


def test_web_tool_schema_exposes_source_authority_policy():
    from agent.runtime.ssot_runtime import _build_ssot_runtime_tool_registry
    from core.runtime_engine.query_loop import _build_cached_tool_definitions

    registry = _build_ssot_runtime_tool_registry(["web.manage"])
    tool = _build_cached_tool_definitions(registry)[0]["function"]
    profile = tool["parameters"]["properties"]["authority_profile"]

    assert "network_vendor" in profile["enum"]
    assert "security_advisory" in profile["enum"]
    assert "search finds candidates" in tool["description"]
