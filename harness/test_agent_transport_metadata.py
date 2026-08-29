from __future__ import annotations

from backend.core.agent_contract import normalize_metadata, resolve_workbench_metadata


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


def test_workbench_selection_is_public_but_resolved_context_cannot_be_forged(monkeypatch):
    selection = {"extension_id": "network.operations", "skill_id": "skill_1", "resource_ids": ["device_1"]}
    metadata = normalize_metadata(
        {"workbench_selection": selection, "workbench_context": {"connection_ids": ["forged"]}},
        transport="http",
        stream_mode="sync",
    )
    assert metadata["workbench_selection"] == selection
    assert "workbench_context" not in metadata

    monkeypatch.setattr(
        "extensions.runtime.resolve_workbench_context",
        lambda workspace_id, value: {"extension_id": value["extension_id"], "skill_id": value["skill_id"], "connection_ids": [f"verified:{workspace_id}"]},
    )
    resolved = resolve_workbench_metadata(metadata, "workspace_1")
    assert "workbench_selection" not in resolved
    assert resolved["workbench_context"]["connection_ids"] == ["verified:workspace_1"]
