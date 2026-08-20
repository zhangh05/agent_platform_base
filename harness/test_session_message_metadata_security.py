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
