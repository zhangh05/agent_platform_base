"""Focused contracts for pasted-image chat attachments."""

from pathlib import Path

import pytest


@pytest.fixture
def image_attachment(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
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


def test_websocket_attachment_validation_uses_authenticated_user_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
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
        extra={"stream_scope": "planner", "vision_attachments": [{"file_id": "file_x", "kind": "image"}]},
    )
    content = captured["messages"][1].content
    assert result.content == "ok"
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "look at this"}
    assert content[1]["type"] == "image_url"


def test_minimax_m3_is_treated_as_a_vision_model():
    from agent.llm.capabilities import supports_vision

    assert supports_vision({"provider": "minimax", "model": "MiniMax-M3"}) is True
    assert supports_vision({"model": "gpt-4o-mini"}) is True
