from __future__ import annotations

import json

from agent.llm.prompt_assembly import (
    build_prompt_profile,
    cache_strategy,
    normalize_usage,
    split_stable_system,
)
from agent.llm.provider import _to_anthropic_messages_request, _to_openai_compatible_messages, generate
from agent.llm.schemas import LLMMessage, LLMRequest


def _request(system: str = "stable") -> LLMRequest:
    return LLMRequest(
        task="assistant_chat",
        messages=[
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content="<current_user_request>hello</current_user_request>"),
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": "system__manage",
                "description": "system facts",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
    )


def test_prompt_profile_is_provider_neutral_and_content_safe():
    request = _request()
    anthropic = build_prompt_profile(request, {
        "provider": "anthropic", "provider_type": "anthropic_messages", "model": "claude",
    })
    openai = build_prompt_profile(request, {
        "provider": "openai", "provider_type": "openai_compatible", "model": "gpt",
    })
    assert anthropic["stable_prefix_fingerprint"] == openai["stable_prefix_fingerprint"]
    assert anthropic["strategy"] == "anthropic_explicit"
    assert openai["strategy"] == "openai_automatic"
    assert anthropic["layers"]["current_request"]["present"] is True
    assert "hello" not in json.dumps(anthropic)


def test_subagent_assignment_is_after_stable_cache_boundary():
    system = "stable kernel\n\n## Subagent assignment\n- Role: reviewer"
    assert split_stable_system(system) == (
        "stable kernel", "## Subagent assignment\n- Role: reviewer",
    )
    request = _request(system)
    anthropic = _to_anthropic_messages_request(request, {
        "provider": "anthropic", "model": "claude",
    })
    assert anthropic["system"][0]["text"] == "stable kernel"
    assert anthropic["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert anthropic["system"][1] == {
        "type": "text", "text": "## Subagent assignment\n- Role: reviewer",
    }
    openai = _to_openai_compatible_messages(request.messages)
    assert openai[:2] == [
        {"role": "system", "content": "stable kernel"},
        {"role": "system", "content": "## Subagent assignment\n- Role: reviewer"},
    ]


def test_subagent_variants_share_stable_prefix_but_not_full_assembly():
    first = build_prompt_profile(
        _request("stable\n\n## Subagent assignment\n- Role: reviewer"),
        {"provider": "openai", "provider_type": "openai_compatible"},
    )
    second = build_prompt_profile(
        _request("stable\n\n## Subagent assignment\n- Role: analyst"),
        {"provider": "openai", "provider_type": "openai_compatible"},
    )
    assert first["stable_prefix_fingerprint"] == second["stable_prefix_fingerprint"]
    assert first["assembly_fingerprint"] != second["assembly_fingerprint"]


def test_selected_skill_is_detected_without_exposing_skill_text():
    request = _request()
    request.messages[-1].content = (
        '<runtime_guidance trusted="true" source_kind="workbench_skill">secret device context</runtime_guidance>\n'
        '<current_user_request>inspect</current_user_request>'
    )
    profile = build_prompt_profile(
        request, {"provider": "anthropic", "provider_type": "anthropic_messages"}
    )
    assert profile["selected_skill"] is True
    assert profile["layers"]["selected_skill"]["present"] is True
    assert "secret device context" not in json.dumps(profile)


def test_runtime_guidance_detection_is_attribute_order_independent():
    request = _request()
    request.messages[-1].content = (
        '<runtime_guidance source_kind="workbench_skill" version="2" trusted="true">secret</runtime_guidance>\n'
        '<runtime_identity version="2">workspace=alpha</runtime_identity>\n'
        '<current_user_request>inspect</current_user_request>'
    )
    profile = build_prompt_profile(
        request, {"provider": "openai", "provider_type": "openai_compatible"}
    )
    assert profile["selected_skill"] is True
    assert profile["layers"]["runtime_identity"]["present"] is True
    assert profile["cache_shard"] >= 0
    assert "secret" not in json.dumps(profile)


def test_compatible_gateways_do_not_receive_openai_private_cache_fields(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        encoding = "utf-8"

        @staticmethod
        def iter_lines(decode_unicode=True):
            return [
                'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"model":"deepseek"}',
                "data: [DONE]",
            ]

    def post(_url, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("requests.post", post)
    request = _request(); request.stream = True
    response = generate(request, {
        "enabled": True,
        "provider": "deepseek",
        "provider_type": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "test",
    })
    assert response.content == "ok"
    assert "prompt_cache_key" not in captured
    assert "stream_options" not in captured
    assert response.metadata["prompt_cache_strategy"] == "compatible_prefix_only"


def test_official_openai_uses_cache_key_and_usage_only_stream_chunk(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        encoding = "utf-8"

        @staticmethod
        def iter_lines(decode_unicode=True):
            return [
                'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"model":"gpt"}',
                'data: {"choices":[],"usage":{"prompt_tokens":1200,"completion_tokens":2,"prompt_tokens_details":{"cached_tokens":1024}}}',
                "data: [DONE]",
            ]

    def post(_url, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("requests.post", post)
    request = _request(); request.stream = True
    response = generate(request, {
        "enabled": True,
        "provider": "openai",
        "provider_type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "test",
    })
    assert captured["prompt_cache_key"].startswith("lzcore.prompt.v2:")
    assert captured["stream_options"] == {"include_usage": True}
    assert response.usage["logical_input_tokens"] == 1200
    assert response.usage["cache_read_input_tokens"] == 1024
    assert response.usage["cache_hit_ratio"] > 0.85
    assert response.metadata["prompt_cache_strategy"] == "openai_automatic"


def test_openai_optional_cache_fields_fall_back_without_prompt_change(monkeypatch):
    payloads = []

    class Response:
        status_code = 200
        encoding = "utf-8"

        @staticmethod
        def iter_lines(decode_unicode=True):
            return ['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"]

    class Rejected:
        status_code = 400
        text = "unsupported prompt_cache_key"
        encoding = "utf-8"

    def post(_url, **kwargs):
        payloads.append(kwargs["json"])
        return Rejected() if len(payloads) == 1 else Response()

    monkeypatch.setattr("requests.post", post)
    request = _request(); request.stream = True
    response = generate(request, {
        "enabled": True,
        "provider": "openai",
        "provider_type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "test",
    })
    assert response.content == "ok"
    assert "prompt_cache_key" in payloads[0]
    assert "prompt_cache_key" not in payloads[1]
    assert payloads[0]["messages"] == payloads[1]["messages"]
    assert response.metadata["prompt_cache_fallback"] is True


def test_usage_normalization_does_not_double_count_openai_cached_tokens():
    normalized = normalize_usage({
        "prompt_tokens": 2000,
        "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 0},
    })
    assert normalized["logical_input_tokens"] == 2000
    assert normalized["uncached_input_tokens"] == 976
    assert normalized["cache_read_input_tokens"] == 1024
    assert normalized["normalized_output_tokens"] == 10


def test_cache_strategy_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LZCORE_PROMPT_CACHE_ENABLED", "false")
    assert cache_strategy({
        "provider": "openai",
        "provider_type": "openai_compatible",
        "prompt_cache_enabled": True,
    }) == "disabled"


def test_provider_can_disable_cache_when_global_default_is_enabled(monkeypatch):
    monkeypatch.setenv("LZCORE_PROMPT_CACHE_ENABLED", "true")
    assert cache_strategy({
        "provider": "anthropic",
        "provider_type": "anthropic_messages",
        "prompt_cache_enabled": False,
    }) == "disabled"


def test_provider_runtime_config_preserves_per_provider_cache_switch():
    from agent.llm.settings import _provider_runtime_config

    cfg = _provider_runtime_config(
        "openai",
        {"enabled": True, "model": "gpt", "prompt_cache_enabled": False},
        "test-key",
    )
    assert cfg["prompt_cache_enabled"] is False
    assert cache_strategy(cfg) == "disabled"


def test_provider_setting_rejects_non_boolean_cache_switch():
    from agent.llm.settings import validate_llm_settings

    errors = validate_llm_settings({
        "provider": "openai",
        "model": "gpt",
        "prompt_cache_enabled": "false",
    })
    assert "prompt_cache_enabled must be boolean" in errors
