from __future__ import annotations

import json


def test_session_metadata_is_deeply_redacted_before_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))

    from storage.paths import workspace_root
    from storage.session_store import create_session

    secret = "sk-test-secret-abcdefghijklmnopqrstuvwxyz"
    session = create_session(
        "default",
        metadata={
            "provider": {"Authorization": f"Bearer {secret}"},
            "tool_context": {"api_key": secret},
        },
    )

    path = workspace_root("default") / "sessions" / f"{session['session_id']}.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(persisted, ensure_ascii=False)
    assert secret not in serialized
    assert "[REDACTED_SECRET]" in serialized


def test_legacy_session_metadata_is_redacted_on_read(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))

    from storage.paths import workspace_root
    from storage.session_store import get_session, list_sessions
    from storage.workspace_store import ensure_workspace

    secret = "sk-test-secret-abcdefghijklmnopqrstuvwxyz"
    workspace_id = "default"
    session_id = "legacy-session"
    ensure_workspace(workspace_id)
    path = workspace_root(workspace_id) / "sessions" / f"{session_id}.json"
    path.write_text(json.dumps({
        "session_id": session_id,
        "workspace_id": workspace_id,
        "title": "Legacy session",
        "status": "active",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "run_ids": [],
        "metadata": {"Authorization": f"Bearer {secret}"},
    }), encoding="utf-8")

    assert secret not in json.dumps(get_session(session_id, workspace_id), ensure_ascii=False)
    assert secret not in json.dumps(list_sessions(workspace_id), ensure_ascii=False)


def test_sessions_are_isolated_by_storage_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))

    from storage.principal import storage_principal
    from storage.session_store import create_session, get_session, list_sessions

    workspace_id = "shared"
    with storage_principal("alice"):
        created = create_session(workspace_id, title="Alice private work")

    with storage_principal("bob"):
        assert get_session(created["session_id"], workspace_id) is None
        assert all(item["session_id"] != created["session_id"] for item in list_sessions(workspace_id))
