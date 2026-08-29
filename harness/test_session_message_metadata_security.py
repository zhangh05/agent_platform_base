from __future__ import annotations

import json


def test_message_metadata_is_deeply_redacted_before_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))

    from storage.message_store import SessionMessageStore

    secret = "sk-test-secret-abcdefghijklmnopqrstuvwxyz"
    store = SessionMessageStore(session_id="session-metadata", ws_id="default")
    store.write_message(
        "run_metadata",
        "assistant",
        "已完成受控诊断。",
        metadata={
            "llm_metadata": {"headers": {"Authorization": f"Bearer {secret}"}},
            "tool_context": {"api_key": secret},
        },
    )

    persisted = json.loads(store._msg_path("run_metadata", "assistant").read_text(encoding="utf-8"))
    serialized = json.dumps(persisted, ensure_ascii=False)
    assert secret not in serialized
    assert "[REDACTED_SECRET]" in serialized


def test_server_validated_workbench_skill_metadata_is_returned_to_chat_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))

    from agent.runtime.message_identity import workbench_message_metadata
    from storage.message_store import SessionMessageStore

    store = SessionMessageStore(session_id="session-skill", ws_id="default")
    metadata = workbench_message_metadata({
        "workbench_context": {"skill_id": "skill-1", "skill_name": "生产巡检"},
    })
    store.write_message("run_skill", "user", "检查设备", metadata={"created_at": "2026-08-29T10:00:00Z", **metadata})

    message = store.get_messages()[0]
    assert message["metadata"]["workbench_skill"] == {"skill_id": "skill-1", "name": "生产巡检"}
