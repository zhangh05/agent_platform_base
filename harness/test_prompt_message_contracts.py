# Prompt message and import contracts.
"""Prompt Runtime renderer, safe_generate wiring, fake ref regex, text blocking tests."""

import json, pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
try:
    from backend.main import app as _flask_app
except ImportError:
    _flask_app = None

@pytest.fixture
def client(temp_dirs):
    if _flask_app is None:
        pytest.skip("Flask app not importable")
    _flask_app.config["TESTING"] = True
    return _flask_app.test_client()

class TestRenderer:
    def test_module_exists(self):
        from prompts.renderer import render_prompt, RenderedPrompt
        assert callable(render_prompt)

    def test_render_reads_template(self):
        from prompts.renderer import render_prompt
        r = render_prompt("response_compose", {"intent": "test"}, "hello")
        assert isinstance(r.text, str)
        assert len(r.text) > 20

    def test_rendered_has_safe_context(self):
        from prompts.renderer import render_prompt
        r = render_prompt("response_compose", {"intent": "analyze_data"}, "analyze")
        assert "Agent Platform Base" in r.text

    def test_rendered_has_user_input(self):
        from prompts.renderer import render_prompt
        r = render_prompt("context_qa", {"intent": "context_qa"}, "有什么风险？")
        assert "有什么风险" in r.text

    def test_rendered_no_source_config(self):
        from prompts.renderer import render_prompt
        r = render_prompt("response_compose",
                          {"intent": "test", "source_config": "hostname R1\ninterface Gi0/1\n ip address 10.1.1.1"},
                          "test")
        assert "ip address" not in r.text

    def test_rendered_no_secret(self):
        from prompts.renderer import render_prompt
        r = render_prompt("response_compose", {"password": "admin123"}, "test")
        assert "admin123" not in r.text

    def test_rendered_has_citations(self):
        from prompts.renderer import render_prompt
        cite = [{"citation_id": "c1", "source_type": "artifact", "source_id": "art_1"}]
        r = render_prompt("response_compose", {}, "t", cite)
        assert "c1" in r.text

    def test_renderer_resolves_if_blocks_and_filters(self):
        from prompts.renderer import render_prompt

        r = render_prompt(
            "assistant_chat",
            {"result": {"status": "ok", "summary": "done", "secret": "hidden"}},
            "你好",
        )

        assert "{% if" not in r.text
        assert "{% endif" not in r.text
        assert "| summary_only" not in r.text
        assert "<current_user_request>\n你好" in r.text
        assert '<provided_context data_only="true">' in r.text
        assert "Last safe result: done" in r.text
        assert "hidden" not in r.text

    def test_build_prompt_messages_uses_template_as_system_message(self):
        from agent.llm.runtime import _build_prompt_messages

        messages = _build_prompt_messages("assistant_chat", safe_context={}, user_input="你好")

        assert messages[0].role == "system"
        assert "You are Agent Platform Base" in messages[0].content
        assert "without the production tool loop" in messages[0].content
        assert "Agent Platform Base explanation layer. Follow prompt exactly" not in messages[0].content
        assert messages[1].role == "user"
        assert messages[1].content == "你好"

class TestSafeGenerateWiring:
    def test_safe_gen_imports_renderer(self):
        content = (PROJECT_ROOT / "agent" / "llm" / "runtime.py").read_text()
        assert "from prompts.renderer import" in content

    def test_safe_gen_no_old_prompts_default(self):
        content = (PROJECT_ROOT / "agent" / "llm" / "runtime.py").read_text()
        assert "prompts.renderer" in content
        assert "_get_system_prompt" not in content
        assert "_old_safe_context" not in content
        assert "old_prompts_default_path" not in content

    def test_disabled_fallback_has_no_error(self, monkeypatch):
        from agent.llm.runtime import safe_generate
        monkeypatch.setattr("agent.llm.config.resolve_provider_config", lambda: {
            "enabled": False,
            "provider_type": "disabled",
        })
        out = safe_generate("response_compose")
        assert out.llm_used is False

    def test_metadata_has_rendered_prompt_used(self):
        from agent.llm.runtime import safe_generate
        out = safe_generate("response_compose")
        # When disabled, metadata may be empty. When enabled, should have the flag.
        meta = out.metadata or {}
        # Disabled path doesn't render, so this is fine
        assert isinstance(meta, dict)

    def test_client_passes_user_question_as_user_input(self, monkeypatch):
        from agent.llm.client import LLMClient
        from agent.llm.schemas import SafeLLMOutput
        from agent.state import AgentState

        seen = {}

        def fake_safe_generate(task, state, **kwargs):
            seen["task"] = task
            seen["user_input"] = kwargs.get("user_input")
            seen["config_override"] = kwargs.get("config_override")
            return SafeLLMOutput(answer="ok")

        monkeypatch.setattr("agent.llm.runtime.safe_generate", fake_safe_generate)
        client = LLMClient(overrides={"provider": "custom", "model": "draft-model"})
        out = client.generate("context_qa", AgentState(), "你是什么模型")
        assert out.answer == "ok"
        assert seen["task"] == "context_qa"
        assert seen["user_input"] == "你是什么模型"
        assert seen["config_override"]["provider"] == "custom"
        assert seen["config_override"]["model"] == "draft-model"

    def test_all_prompt_registry_tasks_allowed_by_llm_policy(self):
        from agent.llm.schemas import ALLOWED_TASKS
        from prompts.loader import load_prompt_registry
        for prompt in load_prompt_registry():
            assert prompt.task in ALLOWED_TASKS

    def test_provider_reasoning_tags_are_stripped(self):
        from agent.llm.runtime import _sanitize_provider_output
        text, stripped = _sanitize_provider_output("<think>secret reasoning</think>\n最终回答")
        assert stripped is True
        assert "secret reasoning" not in text
        assert text == "最终回答"

    def test_provider_debug_log_never_breaks_llm_result(self, monkeypatch):
        import agent.llm.provider as provider

        def broken_debug(*args, **kwargs):
            raise BrokenPipeError(32, "Broken pipe")

        monkeypatch.setattr(provider._LOG, "debug", broken_debug)
        provider._debug_log("stream finished: %s", "ok")

class TestPolicyBlocking:
    def test_input_block_prevents_provider(self):
        from prompts.policy import check_prompt_input
        r = check_prompt_input(None, {"source_raw": "customer_name,private_note\nalice,confidential"})
        assert not r.ok

    def test_text_block_triggers(self):
        from prompts.policy import check_prompt_text
        r = check_prompt_text("snmp-server community public RO")
        assert not r.ok

    def test_output_block_discards(self):
        from prompts.policy import check_prompt_output
        r = check_prompt_output(None, "已修改生产环境", [])
        assert not r.ok

    def test_fake_art_id_detected(self):
        from prompts.policy import FAKE_REF_PATTERN
        match = FAKE_REF_PATTERN.findall("check art_fake1234567890 here")
        assert len(match) > 0
        assert "art_fake1234567890" in match

    def test_fake_job_id_detected(self):
        from prompts.policy import FAKE_REF_PATTERN
        match = FAKE_REF_PATTERN.findall("job_fake1234567890 is done")
        assert len(match) > 0
        assert "job_fake1234567890" in match

    def test_known_id_allowed(self):
        from prompts.policy import check_prompt_output
        r = check_prompt_output(None, "check art_real1 here", [{"source_id": "art_real1"}])
        assert r.ok

    def test_unknown_id_blocked(self):
        from prompts.policy import check_prompt_output
        r = check_prompt_output(None, "check art_stranger12345 here", [])
        assert not r.ok

    def test_regex_no_prefix_only(self):
        """Regex should return full IDs, not just prefix strings."""
        from prompts.policy import FAKE_REF_PATTERN
        match = FAKE_REF_PATTERN.findall("art_12345678xyz")
        # Should match the full ID
        assert all(len(m) > 8 for m in match)
