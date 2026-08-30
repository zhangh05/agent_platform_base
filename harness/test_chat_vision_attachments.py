"""Focused contracts for pasted-image chat attachments."""

from pathlib import Path

import pytest


@pytest.fixture
def image_attachment(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from storage.file_store import import_user_upload

    source = tmp_path / "diagram.png"
    # A small valid PNG header is enough for storage and data-url contract tests.
    source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"image-bytes")
    return import_user_upload(
        "test_ws", str(source), "diagram.png", logical_type="chat_attachment",
        file_kind="png", binary=True, source="test",
    )


def test_image_attachment_is_workspace_validated_and_classified(image_attachment):
    from backend.core.chat_attachments import normalize_chat_attachments

    attachments = normalize_chat_attachments(
        "test_ws", [{"file_id": image_attachment.file_id}],
    )
    assert attachments == [{
        "file_id": image_attachment.file_id,
        "name": "diagram.png",
        "mime_type": "image/png",
        "size_bytes": image_attachment.size_bytes,
        "kind": "image",
    }]


def test_unknown_attachment_is_rejected():
    from backend.core.chat_attachments import normalize_chat_attachments

    with pytest.raises(ValueError, match="attachment_not_found"):
        normalize_chat_attachments("test_ws", [{"file_id": "file_missing"}])


def test_file_attachment_guidance_is_trusted_and_uses_canonical_extract_action():
    from backend.core.chat_attachments import build_attachment_runtime_guidance

    guidance = build_attachment_runtime_guidance([
        {"file_id": "file_manual", "kind": "file"},
        {"file_id": "file_image", "kind": "image"},
    ])

    assert "file_manual" in guidance
    assert "file_image" not in guidance
    assert 'workspace__file(action="extract_document"' in guidance
    assert "extract_document_image" in guidance
    assert "Never infer a workspace path" in guidance


def test_websocket_attachment_validation_uses_authenticated_user_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    from storage.principal import storage_principal
    from storage.file_store import import_user_upload
    from backend.ws.agent_ws import _normalize_ws_attachments

    source = tmp_path / "note.txt"
    source.write_text("private note", encoding="utf-8")
    with storage_principal("alice"):
        record = import_user_upload("team", str(source), "note.txt", logical_type="chat_attachment", file_kind="text")

    assert _normalize_ws_attachments("alice", "team", [{"file_id": record.file_id}])[0]["file_id"] == record.file_id


def test_vision_content_is_ephemeral_data_url(image_attachment):
    from agent.runtime.vision_inputs import build_vision_content

    parts, warnings = build_vision_content(
        [{"file_id": image_attachment.file_id, "kind": "image"}], "test_ws",
    )
    assert warnings == []
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_planner_receives_multimodal_message(monkeypatch):
    from agent.llm.schemas import LLMResponse
    from agent.runtime.ssot_runtime import _invoke_llm_for_ssot_runtime

    captured = {}
    monkeypatch.setattr(
        "agent.runtime.vision_inputs.build_vision_content",
        lambda *_: ([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}], []),
    )
    monkeypatch.setattr(
        "agent.llm.config.resolve_provider_config",
        lambda: {"model": "gpt-4o-mini"},
    )

    def fake_invoke(**kwargs):
        captured.update(kwargs)
        return LLMResponse(content="ok")

    monkeypatch.setattr("agent.llm.runtime.invoke_llm", fake_invoke)
    result = _invoke_llm_for_ssot_runtime(
        system="system", user="look at this", workspace_id="test_ws",
        extra={"stream_scope": "planner", "evidence_parts": [_image_evidence("ev_x", "file_x")]},
    )
    content = captured["messages"][1].content
    assert result.content == "ok"
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "look at this"}
    assert content[1]["type"] == "image_url"


def test_continuation_preserves_native_tool_messages_and_receives_derived_image(monkeypatch):
    from agent.llm.schemas import LLMMessage, LLMResponse
    from agent.runtime.ssot_runtime import _invoke_llm_for_ssot_runtime

    captured = {}
    monkeypatch.setattr(
        "agent.runtime.vision_inputs.build_vision_content",
        lambda *_: ([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}], []),
    )

    def fake_invoke(**kwargs):
        captured.update(kwargs)
        return LLMResponse(content="ok")

    monkeypatch.setattr("agent.llm.runtime.invoke_llm", fake_invoke)
    runtime_messages = [
        LLMMessage(role="system", content="system"),
        LLMMessage(role="user", content="inspect"),
        LLMMessage(role="assistant", content="", tool_calls=[{
            "id": "call-1", "type": "function",
            "function": {"name": "workspace__file", "arguments": "{}"},
        }]),
        LLMMessage(role="tool", content='{"ok": true}', tool_call_id="call-1"),
    ]

    _invoke_llm_for_ssot_runtime(
        system="system", user="flattened fallback", messages=runtime_messages,
        workspace_id="test_ws",
        extra={
            "stream_scope": "continuation",
            "evidence_parts": [_image_evidence("ev_derived", "file_x")],
        },
    )

    messages = captured["messages"]
    assert [message.role for message in messages] == ["system", "user", "assistant", "tool"]
    assert messages[-1].tool_call_id == "call-1"
    assert isinstance(messages[1].content, list)
    assert messages[1].content[1]["type"] == "image_url"


def test_minimax_m3_is_treated_as_a_vision_model():
    from agent.llm.capabilities import supports_vision

    assert supports_vision({"provider": "minimax", "model": "MiniMax-M3"}) is True
    assert supports_vision({"model": "gpt-4o-mini"}) is True


def test_planner_vision_capability_follows_routed_model(monkeypatch):
    from agent.llm.schemas import LLMResponse
    from agent.runtime.ssot_runtime import _invoke_llm_for_ssot_runtime

    captured = {}
    monkeypatch.setattr(
        "agent.llm.config.resolve_provider_config",
        lambda: {"enabled": True, "provider": "active", "model": "text-only"},
    )
    monkeypatch.setattr(
        "agent.llm.router.resolve_model_candidates",
        lambda _task, _active: [{
            "enabled": True, "provider": "routed", "model": "MiniMax-M3",
        }],
    )
    monkeypatch.setattr(
        "agent.runtime.vision_inputs.build_vision_content",
        lambda *_: ([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}], []),
    )

    def fake_invoke(**kwargs):
        captured.update(kwargs)
        return LLMResponse(content="ok")

    monkeypatch.setattr("agent.llm.runtime.invoke_llm", fake_invoke)
    _invoke_llm_for_ssot_runtime(
        system="system", user="inspect", workspace_id="test_ws",
        extra={"stream_scope": "planner", "evidence_parts": [_image_evidence("ev_x", "file_x")]},
    )

    assert isinstance(captured["messages"][-1].content, list)


def test_planner_does_not_send_image_to_routed_text_model(monkeypatch):
    from agent.llm.schemas import LLMResponse
    from agent.runtime.ssot_runtime import _invoke_llm_for_ssot_runtime

    captured = {}
    monkeypatch.setattr(
        "agent.llm.config.resolve_provider_config",
        lambda: {"enabled": True, "provider": "active", "model": "MiniMax-M3"},
    )
    monkeypatch.setattr(
        "agent.llm.router.resolve_model_candidates",
        lambda _task, _active: [{
            "enabled": True, "provider": "routed", "model": "text-only",
        }],
    )
    monkeypatch.setattr(
        "agent.runtime.vision_inputs.build_vision_content",
        lambda *_: (_ for _ in ()).throw(AssertionError("image must not be built")),
    )

    def fake_invoke(**kwargs):
        captured.update(kwargs)
        return LLMResponse(content="ok")

    monkeypatch.setattr("agent.llm.runtime.invoke_llm", fake_invoke)
    _invoke_llm_for_ssot_runtime(
        system="system", user="inspect", workspace_id="test_ws",
        extra={"stream_scope": "planner", "evidence_parts": [_image_evidence("ev_x", "file_x")]},
    )

    assert isinstance(captured["messages"][-1].content, str)
    assert captured["extra"]["vision_warnings"]


def _image_evidence(evidence_id: str, file_id: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "kind": "image",
        "reference": {"kind": "managed_file", "file_id": file_id},
        "consumer": "llm",
        "delivery_status": "pending",
    }


def test_anthropic_transport_translates_query_loop_tool_messages_to_native_blocks():
    from agent.llm.provider import _to_anthropic_messages_request
    from agent.llm.schemas import LLMMessage, LLMRequest

    request = LLMRequest(
        task="assistant_chat",
        messages=[
            LLMMessage(role="system", content="system"),
            LLMMessage(role="user", content="分析文档"),
            LLMMessage(role="assistant", content="", tool_calls=[{
                "id": "call_extract",
                "type": "function",
                "function": {
                    "name": "workspace__file",
                    "arguments": '{"action":"extract_document_images","file_id":"file_doc"}',
                },
            }]),
            LLMMessage(role="tool", content='{"ok":true}', tool_call_id="call_extract"),
            LLMMessage(role="user", content=[
                {"type": "text", "text": "请基于图片回答"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ]),
        ],
    )
    body = _to_anthropic_messages_request(request, {"model": "MiniMax-M3", "max_tokens": 1000})

    assert body["system"] == [{
        "type": "text",
        "text": "system",
        "cache_control": {"type": "ephemeral"},
    }]
    assert [message["role"] for message in body["messages"]] == [
        "user", "assistant", "user",
    ]
    assert body["messages"][1]["content"][0] == {
        "type": "tool_use",
        "id": "call_extract",
        "name": "workspace__file",
        "input": {"action": "extract_document_images", "file_id": "file_doc"},
    }
    final_blocks = body["messages"][2]["content"]
    assert final_blocks[0] == {
        "type": "tool_result",
        "tool_use_id": "call_extract",
        "content": '{"ok":true}',
    }
    assert final_blocks[-1]["type"] == "image"


def test_minimax_m3_text_turn_uses_anthropic_messages_transport(monkeypatch):
    from agent.llm.provider import generate
    from agent.llm.schemas import LLMMessage, LLMRequest

    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "content": [{"type": "text", "text": "ok"}],
                "model": "MiniMax-M3",
                "stop_reason": "end_turn",
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    response = generate(
        LLMRequest(
            task="assistant_chat",
            messages=[LLMMessage(role="user", content="hello")],
        ),
        {
            "enabled": True,
            "provider": "minimax",
            "provider_type": "anthropic_messages",
            "base_url": "https://api.minimaxi.com/anthropic/v1",
            "model": "MiniMax-M3",
            "api_key": "sk-test",
        },
    )

    assert response.content == "ok"
    assert captured["url"] == "https://api.minimaxi.com/anthropic/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["json"]["messages"] == [{
        "role": "user",
        "content": [{"type": "text", "text": "hello"}],
    }]


def test_anthropic_messages_url_accepts_base_or_full_endpoint():
    from agent.llm.provider import _anthropic_messages_url

    assert _anthropic_messages_url({
        "provider": "minimax",
        "base_url": "https://api.minimaxi.com/v1",
    }) == "https://api.minimaxi.com/anthropic/v1/messages"
    assert _anthropic_messages_url({
        "provider": "minimax",
        "base_url": "https://api.minimaxi.com/anthropic/v1/messages",
    }) == "https://api.minimaxi.com/anthropic/v1/messages"
    assert _anthropic_messages_url({
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
    }) == "https://api.anthropic.com/v1/messages"


def test_anthropic_prompt_cache_can_be_disabled(monkeypatch):
    from agent.llm.provider import _to_anthropic_messages_request
    from agent.llm.schemas import LLMMessage, LLMRequest

    monkeypatch.setenv("LZCORE_PROMPT_CACHE_ENABLED", "false")
    body = _to_anthropic_messages_request(
        LLMRequest(task="assistant_chat", messages=[
            LLMMessage(role="system", content="stable"),
            LLMMessage(role="user", content="dynamic"),
        ]),
        {"model": "MiniMax-M3"},
    )
    assert body["system"] == "stable"


def test_anthropic_cache_rejection_retries_without_changing_prompt(monkeypatch):
    from agent.llm.provider import generate
    from agent.llm.schemas import LLMMessage, LLMRequest

    requests = []

    class Response:
        def __init__(self, status_code, text=""):
            self.status_code = status_code
            self.text = text

        def json(self):
            return {
                "content": [{"type": "text", "text": "ok"}],
                "model": "MiniMax-M3",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            }

    def fake_post(_url, **kwargs):
        requests.append(kwargs["json"])
        if len(requests) == 1:
            return Response(400, "cache_control is not supported")
        return Response(200)

    monkeypatch.setattr("requests.post", fake_post)
    response = generate(
        LLMRequest(task="assistant_chat", stream=False, messages=[
            LLMMessage(role="system", content="stable"),
            LLMMessage(role="user", content="dynamic"),
        ]),
        {
            "enabled": True,
            "provider": "minimax",
            "provider_type": "anthropic_messages",
            "base_url": "https://api.minimaxi.com/anthropic/v1",
            "model": "MiniMax-M3",
            "api_key": "sk-test",
        },
    )

    assert response.content == "ok"
    assert len(requests) == 2
    assert isinstance(requests[0]["system"], list)
    assert requests[1]["system"] == "stable"
    assert response.metadata["prompt_cache_fallback"] is True


def test_anthropic_stream_preserves_input_cache_and_output_usage(monkeypatch):
    import json

    from agent.llm.provider import generate
    from agent.llm.schemas import LLMMessage, LLMRequest

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def iter_lines(decode_unicode=True):
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "model": "MiniMax-M3",
                        "usage": {
                            "input_tokens": 1800,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 128,
                        },
                    },
                },
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
            ]
            return [f"data: {json.dumps(event)}" for event in events]

        @staticmethod
        def close():
            return None

    monkeypatch.setattr("requests.post", lambda *_args, **_kwargs: Response())
    response = generate(
        LLMRequest(task="assistant_chat", stream=True, messages=[
            LLMMessage(role="system", content="stable"),
            LLMMessage(role="user", content="dynamic"),
        ]),
        {
            "enabled": True,
            "provider": "minimax",
            "provider_type": "anthropic_messages",
            "base_url": "https://api.minimaxi.com/anthropic/v1",
            "model": "MiniMax-M3",
            "api_key": "sk-test",
        },
    )

    assert response.content == "ok"
    assert response.usage["input_tokens"] == 1800
    assert response.usage["cache_creation_input_tokens"] == 0
    assert response.usage["cache_read_input_tokens"] == 128
    assert response.usage["output_tokens"] == 2
    assert response.usage["logical_input_tokens"] == 1928
    assert response.usage["uncached_input_tokens"] == 1800
    assert response.usage["normalized_output_tokens"] == 2
