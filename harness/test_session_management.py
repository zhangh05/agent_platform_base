"""Session Management — comprehensive tests for conversation session layer.

Covers:
- Session CRUD (create, get, list, update)
- Soft archive / soft delete / permanent delete semantics
- Run-to-session association
- Message recovery from session runs
- Auto-title from user input
- Workspace isolation
"""

import sys
import os
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.session_store import (
    create_session,
    get_session,
    list_sessions,
    update_session,
    archive_session,
    soft_delete_session,
    delete_session_permanently,
    add_run_to_session,
    get_session_messages,
    get_or_create_default_session,
    auto_title_from_input,
    ensure_session,
    get_session_count,
    list_sessions_by_status,
)
from storage.run_record_store import write_run_record
from agent.state import AgentState
from agent.app.session_manager import SessionManager
from agent.protocol.message import UserMessage
from storage.principal import storage_principal

TEST_WS = "session_mgmt_test"


class TestSessionCRUD:
    def test_create_session(self):
        s = create_session(TEST_WS, "Test CRUD")
        assert s["session_id"]
        assert s["title"] == "Test CRUD"
        assert s["status"] == "active"
        assert s["workspace_id"] == TEST_WS
        assert s["run_ids"] == []

    def test_get_session(self):
        s = create_session(TEST_WS, "Get Test")
        fetched = get_session(s["session_id"], TEST_WS)
        assert fetched is not None
        assert fetched["session_id"] == s["session_id"]

    def test_get_session_not_found(self):
        assert get_session("nonexistent123", TEST_WS) is None

    def test_list_sessions_active_only_by_default(self):
        # Clean up: get all active sessions and archive them
        existing = list_sessions(TEST_WS, status="active")
        for sess in existing:
            archive_session(sess["session_id"], TEST_WS)

        s1 = create_session(TEST_WS, "Active 1")
        s2 = create_session(TEST_WS, "Active 2")
        archive_session(s2["session_id"], TEST_WS)

        active = list_sessions(TEST_WS, status="active")
        ids = [s["session_id"] for s in active]
        assert s1["session_id"] in ids
        assert s2["session_id"] not in ids

    def test_list_sessions_archived(self):
        s = create_session(TEST_WS, "Archived Test")
        archive_session(s["session_id"], TEST_WS)
        archived = list_sessions(TEST_WS, status="archived")
        ids = [x["session_id"] for x in archived]
        assert s["session_id"] in ids

    def test_internal_subagent_sessions_hidden_from_lists(self):
        visible = create_session(TEST_WS, "Visible Session")
        hidden = create_session(
            TEST_WS,
            "You are a subagent: Review Agent",
            metadata={"internal": True, "is_subagent": True, "parent_session_id": visible["session_id"]},
        )
        active = list_sessions(TEST_WS, status="active")
        ids = [x["session_id"] for x in active]
        assert visible["session_id"] in ids
        assert hidden["session_id"] not in ids

        counts = get_session_count(TEST_WS)
        assert counts["active"] == len(active)

    def test_update_session_title(self):
        s = create_session(TEST_WS, "Old Title")
        updated = update_session(s["session_id"], TEST_WS, title="New Title")
        assert updated["title"] == "New Title"

    def test_update_session_invalid_status_ignored(self):
        s = create_session(TEST_WS, "Status Test")
        updated = update_session(s["session_id"], TEST_WS, status="invalid_status")
        # Invalid status should be ignored
        assert updated["status"] == "active"


class TestSessionLifecycle:
    def test_archive_session(self):
        s = create_session(TEST_WS, "To Archive")
        archived = archive_session(s["session_id"], TEST_WS)
        assert archived["status"] == "archived"
        fetched = get_session(s["session_id"], TEST_WS)
        assert fetched["status"] == "archived"

    def test_soft_delete_session(self):
        s = create_session(TEST_WS, "To Soft Delete")
        deleted = soft_delete_session(s["session_id"], TEST_WS)
        assert deleted["status"] == "deleted"
        fetched = get_session(s["session_id"], TEST_WS)
        assert fetched["status"] == "deleted"

    def test_permanent_delete_requires_confirm(self):
        s = create_session(TEST_WS, "To Perm Delete")
        ok = delete_session_permanently(s["session_id"], TEST_WS, confirm=False)
        assert ok is False
        fetched = get_session(s["session_id"], TEST_WS)
        assert fetched is not None

    def test_permanent_delete_with_confirm(self):
        s = create_session(TEST_WS, "To Perm Delete")
        ok = delete_session_permanently(s["session_id"], TEST_WS, confirm=True)
        assert ok is True
        fetched = get_session(s["session_id"], TEST_WS)
        assert fetched is None


class TestLiveSessionIsolation:
    def test_same_session_id_isolated_by_storage_principal(self):
        manager = SessionManager()
        with storage_principal("alice"):
            sid, alice_session, _ = manager.get_or_create("session_shared", "team_a")
            alice_session.history.append(UserMessage(content="alice confidential context"))

        with storage_principal("bob"):
            bob_sid, bob_session, _ = manager.get_or_create("session_shared", "team_a")
            assert bob_sid == sid
            assert bob_session is not alice_session
            assert bob_session.history == []

        with storage_principal("alice"):
            _, restored_alice, _ = manager.get_or_create("session_shared", "team_a")
            assert restored_alice is alice_session
            assert restored_alice.history[0].content == "alice confidential context"


class TestSessionRunAssociation:
    def test_run_record_creates_caller_owned_session_before_association(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
        session_id = "browser_session_1"
        state = AgentState(
            request_id="run_first_001",
            user_input="first browser turn",
            intent="assistant_chat",
            workspace_id="default",
            session_id=session_id,
        )

        write_run_record(state, "default")

        fetched = get_session(session_id, "default")
        assert fetched is not None
        assert fetched["run_ids"] == ["run_first_001"]
        assert fetched["title"] == "first browser turn"

    def test_list_repairs_legacy_session_title_and_run_ids(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
        from storage.message_store import SessionMessageStore

        session_id = "legacy_browser_1"
        ensure_session(session_id, "default")
        store = SessionMessageStore(session_id, "default")
        store.write_message(
            "legacy_run_1", "user", "派生子任务查询天气",
            {"created_at": "2026-01-01T00:00:00+00:00"},
        )
        store.write_message(
            "legacy_run_1", "assistant", "处理失败",
            {"created_at": "2026-01-01T00:00:01+00:00"},
        )
        store.write_message(
            "legacy_run_2", "user", "不可能啊",
            {"created_at": "2026-01-01T00:00:02+00:00"},
        )
        store.write_message(
            "legacy_run_2", "assistant", "继续处理",
            {"created_at": "2026-01-01T00:00:03+00:00"},
        )
        update_session(
            session_id, "default", title=session_id,
            metadata={"auto_repaired": True},
        )

        repaired = next(
            session for session in list_sessions("default")
            if session["session_id"] == session_id
        )

        assert repaired["title"] == "派生子任务查询天气"
        assert repaired["run_ids"] == ["legacy_run_1", "legacy_run_2"]
        assert repaired["metadata"]["repair_complete"] is True

    def test_add_run_to_session(self):
        s = create_session(TEST_WS, "Run Assoc")
        updated = add_run_to_session(s["session_id"], "run_001", TEST_WS)
        assert "run_001" in updated["run_ids"]

    def test_add_run_duplicate_ignored(self):
        s = create_session(TEST_WS, "Run Dup")
        add_run_to_session(s["session_id"], "run_001", TEST_WS)
        updated = add_run_to_session(s["session_id"], "run_001", TEST_WS)
        assert updated["run_ids"].count("run_001") == 1

    def test_run_record_auto_associates_with_session(self):
        s = create_session(TEST_WS, "Auto Assoc")
        state = AgentState(
            request_id="run_auto_001",
            user_input="test input",
            intent="assistant_chat",
            workspace_id=TEST_WS,
            session_id=s["session_id"],
        )
        run_id = write_run_record(state, TEST_WS)
        fetched = get_session(s["session_id"], TEST_WS)
        assert run_id in fetched["run_ids"]

    def test_run_record_without_session_no_association(self):
        state = AgentState(
            request_id="run_no_session_001",
            user_input="test input",
            intent="assistant_chat",
            workspace_id=TEST_WS,
            session_id=None,
        )
        run_id = write_run_record(state, TEST_WS)
        # Should not crash; session should not be modified
        assert run_id


class TestSessionMessages:
    def test_get_session_messages_empty(self):
        s = create_session(TEST_WS, "Empty Messages")
        msgs = get_session_messages(s["session_id"], TEST_WS)
        assert msgs == []

    def test_get_session_messages_from_canonical_store(self):
        from storage.message_store import SessionMessageStore

        s = create_session(TEST_WS, "Messages Test")
        store = SessionMessageStore(s["session_id"], TEST_WS)
        for i in range(2):
            run_id = f"run_msg_{i}"
            metadata = {"created_at": f"2026-01-01T00:00:0{i}Z"}
            store.write_message(run_id, "user", f"question {i}", metadata)
            store.write_message(run_id, "assistant", f"answer {i}", metadata)

        msgs = get_session_messages(s["session_id"], TEST_WS)
        assert len(msgs) == 4
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_repeated_session_updates_replace_existing_record(self):
        s = create_session(TEST_WS, "First")
        update_session(s["session_id"], TEST_WS, title="Second")
        update_session(s["session_id"], TEST_WS, title="Third")

        assert get_session(s["session_id"], TEST_WS)["title"] == "Third"

    def test_get_session_messages_falls_back_to_linked_run_records(self):
        s = create_session(TEST_WS, "Run-backed Messages")
        state = AgentState(
            request_id="run_message_fallback",
            user_input="为什么运行记录没有显示在对话里？",
            intent="assistant_chat",
            workspace_id=TEST_WS,
            session_id=s["session_id"],
        )
        state.final_response = "因为消息目录缺失，应该从关联运行记录读取。"
        write_run_record(state, TEST_WS)

        msgs = get_session_messages(s["session_id"], TEST_WS)

        assert [(m["role"], m["content"]) for m in msgs] == [
            ("user", "为什么运行记录没有显示在对话里？"),
            ("assistant", "因为消息目录缺失，应该从关联运行记录读取。"),
        ]

    def test_deleted_session_does_not_fall_back_to_run_records(self):
        s = create_session(TEST_WS, "Deleted Run-backed Messages")
        state = AgentState(
            request_id="run_deleted_session",
            user_input="这条记录不应恢复被删除的会话。",
            intent="assistant_chat",
            workspace_id=TEST_WS,
            session_id=s["session_id"],
        )
        state.final_response = "删除后不再显示。"
        write_run_record(state, TEST_WS)
        delete_session_permanently(s["session_id"], TEST_WS, confirm=True)

        assert get_session_messages(s["session_id"], TEST_WS) == []


class TestSessionAutoTitle:
    def test_auto_title_from_first_input(self):
        s = create_session(TEST_WS, "新会话")
        new_title = auto_title_from_input(s["session_id"], "What is OSPF?", TEST_WS)
        assert new_title == "What is OSPF?"
        fetched = get_session(s["session_id"], TEST_WS)
        assert fetched["title"] == "What is OSPF?"

    def test_auto_title_truncates_long_input(self):
        s = create_session(TEST_WS, "新会话")
        long_input = "A" * 50
        new_title = auto_title_from_input(s["session_id"], long_input, TEST_WS)
        assert new_title.endswith("...")
        assert len(new_title) <= 24  # 20 + "..."

    def test_auto_title_skips_non_generic_titles(self):
        s = create_session(TEST_WS, "Custom Title")
        result = auto_title_from_input(s["session_id"], "Some input", TEST_WS)
        assert result is None  # Should not overwrite custom title


class TestSessionWorkspaceIsolation:
    def test_sessions_isolated_by_workspace(self):
        s1 = create_session("ws_a", "WS A Session")
        s2 = create_session("ws_b", "WS B Session")

        a_sessions = list_sessions("ws_a", status="active")
        b_sessions = list_sessions("ws_b", status="active")

        a_ids = [s["session_id"] for s in a_sessions]
        b_ids = [s["session_id"] for s in b_sessions]

        assert s1["session_id"] in a_ids
        assert s2["session_id"] not in a_ids
        assert s2["session_id"] in b_ids
        assert s1["session_id"] not in b_ids


class TestSessionCounts:
    def test_get_session_count(self):
        # Clean workspace first
        for s in list_sessions(TEST_WS, status="active"):
            soft_delete_session(s["session_id"], TEST_WS)
        for s in list_sessions(TEST_WS, status="archived"):
            soft_delete_session(s["session_id"], TEST_WS)
        for s in list_sessions(TEST_WS, status="deleted"):
            delete_session_permanently(s["session_id"], TEST_WS, confirm=True)

        create_session(TEST_WS, "Count Active")
        s2 = create_session(TEST_WS, "Count Archived")
        archive_session(s2["session_id"], TEST_WS)
        s3 = create_session(TEST_WS, "Count Deleted")
        soft_delete_session(s3["session_id"], TEST_WS)

        counts = get_session_count(TEST_WS)
        assert counts["active"] >= 1
        assert counts["archived"] >= 1
        assert counts["deleted"] >= 1
        assert counts["total"] >= 3


class TestSessionDefault:
    def test_get_or_create_default_session(self):
        # First call should create a new session if none exist
        s = get_or_create_default_session("default_get_or_create")
        assert s["status"] == "active"
        # Second call should return the same session
        s2 = get_or_create_default_session("default_get_or_create")
        assert s2["session_id"] == s["session_id"]


if __name__ == "__main__":
    # Run all tests manually
    classes = [
        TestSessionCRUD,
        TestSessionLifecycle,
        TestSessionRunAssociation,
        TestSessionMessages,
        TestSessionAutoTitle,
        TestSessionWorkspaceIsolation,
        TestSessionCounts,
        TestSessionDefault,
    ]
    total = 0
    passed = 0
    failed = 0
    for cls in classes:
        inst = cls()
        for name in dir(inst):
            if name.startswith("test_"):
                total += 1
                try:
                    getattr(inst, name)()
                    passed += 1
                    print(f"  PASS: {cls.__name__}.{name}")
                except Exception as e:
                    failed += 1
                    print(f"  FAIL: {cls.__name__}.{name} — {e}")
    print(f"\n{total} tests, {passed} passed, {failed} failed")
