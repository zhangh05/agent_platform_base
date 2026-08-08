"""Tests for LLM streaming scope isolation and direct-answer fast path.

v3.11: verifies that planner tokens do NOT leak to the user token
channel, direct-answer / response tokens DO, and that the narrow
fast-path classifier correctly routes simple queries.
"""

import asyncio
import pytest
from unittest import mock
from types import SimpleNamespace

from core.runtime_engine.fast_path import classify_direct_answer, FastPathDecision
from core.runtime_engine import SSOTRuntimeConfig, SSOTRuntimeEngine


# ============================================================================
# Fast-path classifier unit tests
# ============================================================================

class TestFastPathClassifier:
    """classify_direct_answer() standalone tests."""

    def test_greeting_gets_fast_path(self):
        d = classify_direct_answer("你好")
        assert d.enabled is True
        assert d.route == "greeting"

    def test_hellogets_fast_path(self):
        d = classify_direct_answer("hello")
        assert d.enabled is True

    def test_definition_gets_fast_path(self):
        d = classify_direct_answer("解释一下 OSPF 是什么")
        assert d.enabled is True
        assert d.route == "simple_question"

    def test_what_is_fast_path(self):
        d = classify_direct_answer("NAT 是什么")
        assert d.enabled is True

    def test_explicit_web_search_rejects_fast_path(self):
        d = classify_direct_answer("我先测试你的联网搜索，联网搜索一下什么是 K8s")
        assert d.enabled is False
        assert d.reason == "hard_tool_keyword"

    def test_ospf_neighbor_down_rejects(self):
        d = classify_direct_answer("OSPF 邻居起不来，帮我排查")
        assert d.enabled is False

    def test_read_file_rejects(self):
        d = classify_direct_answer("帮我读取 README.md")
        assert d.enabled is False

    def test_check_health_rejects(self):
        d = classify_direct_answer("检查系统健康状态")
        assert d.enabled is False

    def test_ping_rejects(self):
        d = classify_direct_answer("ping 8.8.8.8")
        assert d.enabled is False

    def test_translate_fast_path(self):
        d = classify_direct_answer("翻译这段英语")
        assert d.enabled is True

    def test_rewrite_fast_path(self):
        d = classify_direct_answer("帮我润色一下这段话")
        assert d.enabled is True

    def test_empty_input(self):
        d = classify_direct_answer("")
        assert d.enabled is False

    def test_unknown_falls_through(self):
        """Anything that doesn't match a whitelist pattern falls through."""
        d = classify_direct_answer("请帮我检查 Kafka 延迟情况")
        assert d.enabled is False

    # ── v3.11.1: troubleshooting-intent boundary tests ──────────────

    def test_ospf_definition(self):
        """'解释一下 OSPF 是什么' is a pure definition → fast path."""
        d = classify_direct_answer("解释一下 OSPF 是什么")
        assert d.enabled is True

    def test_ospf_neighbor_down_reason(self):
        """'解释一下 OSPF 邻居起不来的原因' has troubleshooting intent → full SSOT Runtime."""
        d = classify_direct_answer("解释一下 OSPF 邻居起不来的原因")
        assert d.enabled is False

    def test_nat_definition(self):
        """'NAT 是什么' → fast path."""
        d = classify_direct_answer("NAT 是什么")
        assert d.enabled is True

    def test_nat_policy_failure(self):
        """'NAT 策略不生效是什么原因' has network+troubleshooting → full SSOT Runtime."""
        d = classify_direct_answer("NAT 策略不生效是什么原因")
        assert d.enabled is False

    def test_mtu_definition(self):
        """'MTU 是什么' → fast path."""
        d = classify_direct_answer("MTU 是什么")
        assert d.enabled is True

    def test_mtu_mismatch(self):
        """'MTU 不匹配是什么现象' has troubleshooting → full SSOT Runtime."""
        d = classify_direct_answer("MTU 不匹配是什么现象")
        assert d.enabled is False

    def test_interface_packet_loss(self):
        """'接口丢包是什么原因' has network+troubleshooting → full SSOT Runtime."""
        d = classify_direct_answer("接口丢包是什么原因")
        assert d.enabled is False

    def test_firewall_policy_analysis(self):
        """'帮我分析防火墙策略不生效' has troubleshooting → full SSOT Runtime."""
        d = classify_direct_answer("帮我分析防火墙策略不生效")
        assert d.enabled is False

    def test_translate_text_fast_path(self):
        """'翻译这段英文' → fast path (pure text task)."""
        d = classify_direct_answer("翻译这段英文")
        assert d.enabled is True

    def test_translate_file(self):
        """'翻译 README.md' or '翻译这个文件' → hard-tool keyword → full SSOT Runtime."""
        d = classify_direct_answer("翻译 README.md")
        assert d.enabled is False
        d2 = classify_direct_answer("翻译这个文件")
        assert d2.enabled is False


# ============================================================================
# SSOT Runtime fast-path integration tests
# ============================================================================

class TestFastPathGenerator:
    """Test the _generate_direct_answer path through SSOTRuntimeEngine."""

    def test_fast_path_skips_planner(self):
        """'你好' uses fast path — planner never invoked."""
        planner_called = []

        def llm_mock(**kwargs):
            planner_called.append(kwargs.get("system", ""))
            return kwargs.get("user", "hello")

        config = SSOTRuntimeConfig()
        engine = SSOTRuntimeEngine(
            config=config, llm_invoke=llm_mock,
            tool_runtime=mock.MagicMock(),
        )

        result = asyncio.run(engine.run(user_input="你好", workspace_id="test"))
        assert result.success

        meta = result.metadata
        assert meta.get("fast_path") is True
        assert meta.get("planner_skipped") is True
        assert meta.get("used_tools") is False
        assert meta.get("route") == "greeting"
        assert meta.get("direct_answer_latency_ms", 0) > 0

    def test_definition_skips_planner(self):
        """'解释一下 OSPF 是什么' uses fast path — planner never invoked."""
        planner_called = []

        def llm_mock(**kwargs):
            extra = kwargs.get("extra") or {}
            planner_called.append(extra.get("stream_scope", "unknown"))
            return "OSPF（开放式最短路径优先）是一种内部网关协议..."

        config = SSOTRuntimeConfig()
        engine = SSOTRuntimeEngine(
            config=config, llm_invoke=llm_mock,
            tool_runtime=mock.MagicMock(),
        )

        result = asyncio.run(engine.run(
            user_input="解释一下 OSPF 是什么",
            workspace_id="test",
        ))
        assert result.success

        meta = result.metadata
        assert meta.get("fast_path") is True
        assert meta.get("planner_skipped") is True
        assert meta.get("used_tools") is False
        assert meta.get("route") == "simple_question"

    def test_what_does_it_mean_uses_history_without_tools(self):
        """'什么意思' after a prior result should explain history, not rerun tools."""
        seen = {}

        def llm_mock(**kwargs):
            seen["system"] = kwargs.get("system", "")
            seen["user"] = kwargs.get("user", "")
            return "上一轮的意思是：工具跑完了，但最终说明不完整。"

        config = SSOTRuntimeConfig()
        engine = SSOTRuntimeEngine(
            config=config, llm_invoke=llm_mock,
            tool_runtime=mock.MagicMock(),
        )

        result = asyncio.run(engine.run(
            user_input="什么意思？",
            workspace_id="test",
            extras={
                "conversation_history_block": (
                    "RECENT CONVERSATION HISTORY:\n"
                    "  [1] user: 对工作区文件发起检查。\n"
                    "  [2] assistant: 工具执行完成：成功 1 个，失败 7 个。"
                )
            },
        ))

        assert result.success
        meta = result.metadata
        assert meta.get("fast_path") is True
        assert meta.get("route") == "conversation_explain"
        assert meta.get("planner_skipped") is True
        assert meta.get("used_tools") is False
        assert meta.get("conversation_comprehension") is True
        assert "RECENT CONVERSATION HISTORY" in seen["user"]
        assert '<conversation_history data_only="true">' in seen["user"]
        assert "data, not instructions" in seen["system"]
        assert result.final_response.startswith("上一轮的意思")

    def test_ospf_neighbor_down_full_ssot_runtime(self):
        """'OSPF 邻居起不来' rejets fast path — planner must run."""
        planner_called = []

        def llm_mock(**kwargs):
            planner_called.append(True)
            return '{"nodes": []}'  # planner returns no tools

        config = SSOTRuntimeConfig()
        engine = SSOTRuntimeEngine(
            config=config, llm_invoke=llm_mock,
            tool_runtime=mock.MagicMock(),
        )

        result = asyncio.run(engine.run(
            user_input="OSPF 邻居起不来，帮我排查",
            workspace_id="test",
        ))
        meta = result.metadata
        assert meta.get("fast_path") is False, "OSPF neighbor down should NOT fast-path"
        assert meta.get("planner_skipped") is False
        assert len(planner_called) >= 1, "planner should have been invoked"

    def test_read_file_full_ssot_runtime(self):
        """'帮我读取 README.md' rejects fast path."""
        planner_called = []

        def llm_mock(**kwargs):
            planner_called.append(True)
            return '{"nodes": []}'

        config = SSOTRuntimeConfig()
        engine = SSOTRuntimeEngine(
            config=config, llm_invoke=llm_mock,
            tool_runtime=mock.MagicMock(),
        )

        result = asyncio.run(engine.run(
            user_input="帮我读取 README.md",
            workspace_id="test",
        ))
        meta = result.metadata
        assert meta.get("fast_path") is False
        assert meta.get("planner_skipped") is False


# ============================================================================
# Stream scope tests
# ============================================================================

class TestStreamScope:
    """Verify stream_to_user / stream_scope reaching provider.py."""

    def test_planner_gets_stream_to_user_false(self):
        """Structured planner scope keeps planner tokens out of user stream."""
        from agent.llm.runtime import invoke_llm

        req_meta = {}

        # Replace invoke_llm temporarily so we can capture the
        # LLMRequest metadata without actually calling an LLM API.
        original = invoke_llm

        def _capture(**ikwargs):
            # We cannot reach req.metadata from here because
            # invoke_llm creates the req internally, but we CAN
            # check what extra is being passed.
            extra_captured = ikwargs.get("extra") or {}
            req_meta["stream_to_user"] = extra_captured.get("stream_to_user")
            req_meta["stream_scope"] = extra_captured.get("stream_scope")
            req_meta["planner"] = extra_captured.get("planner")
            return type("FakeResp", (), {
                "content": "ok",
                "error": None,
            })()

        import agent.llm.runtime as runtime_mod
        runtime_mod.invoke_llm = _capture
        try:
            from agent.runtime.ssot_runtime import _invoke_llm_for_ssot_runtime
            # Simulate a planner call
            _invoke_llm_for_ssot_runtime(
                system="production runtime contract",
                user="test",
                extra={"stream_scope": "planner", "stream_to_user": False},
            )
        finally:
            runtime_mod.invoke_llm = original

        assert req_meta.get("stream_to_user") is False, (
            "planner must have stream_to_user=False"
        )
        assert req_meta.get("stream_scope") == "planner"

    def test_response_scope_gets_stream_to_user_true(self):
        """The QueryLoop response scope must stream tokens to the user."""
        from agent.llm.runtime import invoke_llm

        req_meta = {}
        original = invoke_llm

        def _capture(**ikwargs):
            extra_captured = ikwargs.get("extra") or {}
            req_meta["stream_to_user"] = extra_captured.get("stream_to_user")
            req_meta["stream_scope"] = extra_captured.get("stream_scope")
            return type("FakeResp", (), {
                "content": "ok",
                "error": None,
            })()

        import agent.llm.runtime as runtime_mod
        runtime_mod.invoke_llm = _capture
        try:
            from agent.runtime.ssot_runtime import _invoke_llm_for_ssot_runtime
            # Simulate a response call (no "execution planner" in system)
            _invoke_llm_for_ssot_runtime(
                system="You are a helpful network assistant.",
                user="summarize results",
                extra={"stream_scope": "response", "stream_to_user": True},
            )
        finally:
            runtime_mod.invoke_llm = original

        assert req_meta.get("stream_to_user") is True, (
            "response must have stream_to_user=True"
        )
        assert req_meta.get("stream_scope") == "response"

    def test_direct_answer_overrides_scope(self):
        """When caller provides extra, it overrides auto-detected scope."""
        from agent.llm.runtime import invoke_llm

        req_meta = {}
        original = invoke_llm

        def _capture(**ikwargs):
            extra_captured = ikwargs.get("extra") or {}
            req_meta["stream_to_user"] = extra_captured.get("stream_to_user")
            req_meta["stream_scope"] = extra_captured.get("stream_scope")
            return type("FakeResp", (), {
                "content": "ok",
                "error": None,
            })()

        import agent.llm.runtime as runtime_mod
        runtime_mod.invoke_llm = _capture
        try:
            from agent.runtime.ssot_runtime import _invoke_llm_for_ssot_runtime
            _invoke_llm_for_ssot_runtime(
                system="Direct answer prompt",
                user="what is OSPF",
                extra={
                    "runtime_engine": "ssot_runtime",
                    "stream_scope": "direct_answer",
                    "stream_to_user": True,
                },
            )
        finally:
            runtime_mod.invoke_llm = original

        assert req_meta.get("stream_to_user") is True
        assert req_meta.get("stream_scope") == "direct_answer"
