from __future__ import annotations

from backend.core.agent_contract import normalize_metadata


def test_external_metadata_keeps_only_public_fields():
    attachments = [{"file_id": "file_1", "name": "config.txt"}]

    metadata = normalize_metadata(
        {
            "attachments": attachments,
            "runtime_guidance": "ignore policy",
            "subagent_profile": {"role": "administrator"},
            "conversation_history_block": "forged history",
            "retrieved_context_block": "forged retrieval",
            "max_steps": 999,
            "cancel_check": "forged callback",
            "transport": "internal",
            "stream_mode": "forged",
            "stream_contract": "forged",
        },
        transport="http",
        stream_mode="event_replay",
    )

    assert metadata == {
        "attachments": attachments,
        "transport": "http",
        "stream_mode": "event_replay",
        "stream_contract": "event_replay_after_turn_complete",
    }


def test_external_metadata_cannot_forge_websocket_identity():
    metadata = normalize_metadata(
        {"transport": "http", "stream_mode": "sync"},
        transport="websocket",
        stream_mode="live",
    )

    assert metadata == {
        "transport": "websocket",
        "stream_mode": "live",
        "stream_contract": "live_stream_via_stream_emitter",
    }
