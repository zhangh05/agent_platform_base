"""Production context-path integrity tests."""

import pytest

from agent.runtime.ssot_runtime import (
    _format_recent_history,
    _history_overlap,
    _summarize_older_messages,
)
from core.runtime_engine.context_compaction import history_importance_score
from core.context.context_store import ContextStore
from core.context.unified_retriever import UnifiedRetriever, get_retriever
from core.runtime_engine.prompt_contract import RUNTIME_SYSTEM_PROMPT
from storage.principal import storage_principal


def test_restored_history_overlap_is_not_injected_twice():
    persisted = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    memory = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "next"},
    ]
    assert _history_overlap(persisted, memory) == 2
    assert persisted + memory[2:] == [*persisted, {"role": "user", "content": "next"}]


def test_recent_history_preserves_all_messages():
    messages = [
        {"role": "user", "content": f"old-{index}-" + "x" * 500}
        for index in range(20)
    ]
    text = _format_recent_history(
        messages,
        max_tokens=450,
        per_message_tokens=180,
    )
    assert "old-19-" in text
    assert "old-0-" in text
    from core.runtime_engine.context_budget import estimate_text_tokens
    assert estimate_text_tokens(text) > 450


def test_history_retention_prioritizes_constraints_corrections_and_entities():
    assert history_importance_score("必须保留 VLAN 20，VLAN 10 不允许认证") > history_importance_score("看看数据")
    assert history_importance_score("更正：以 router01.log 为准") >= 7
    assert history_importance_score("今天天气不错") == 0


def test_older_history_summary_is_structured_and_redacted():
    messages = [
        {"role": "user", "content": "随便聊聊"},
        {"role": "user", "content": "必须使用 router01.log，password=hunter2"},
        {"role": "assistant", "content": "已完成 task-123"},
    ]
    summary = _summarize_older_messages(messages, max_tokens=500)
    assert "signals=constraint,artifact,entity" in summary
    assert "task-123" in summary
    assert "hunter2" not in summary
    assert "[REDACTED_SECRET]" in summary


def test_context_store_uses_workspace_storage_root(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    store = ContextStore("alpha")
    assert store._items_path == tmp_path / "alpha" / "context" / "items.jsonl"


def test_context_store_singleton_tracks_workspace_root_changes(monkeypatch, tmp_path):
    from core.context.context_store import get_context_store

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(root_a))
    first = get_context_store("samews")
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(root_b))
    second = get_context_store("samews")

    assert first is not second
    assert first._items_path == root_a / "samews" / "context" / "items.jsonl"
    assert second._items_path == root_b / "samews" / "context" / "items.jsonl"


def test_retriever_cache_isolated_by_storage_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    with storage_principal("alice"):
        alice = get_retriever("team")
        alice._store.put({
            "item_id": "alice-only",
            "item_type": "memory_hit",
            "workspace_id": "team",
            "memory_type": "semantic_fact",
            "memory_status": "active",
            "status": "active",
            "scope": "workspace",
            "content": "alice router credential policy",
        })
        assert alice.search_memory("router credential", top_k=5)

    with storage_principal("bob"):
        bob = get_retriever("team")
        assert bob is not alice
        assert bob._store._items_path != alice._store._items_path
        assert bob.search_memory("router credential", top_k=5) == []


def test_context_store_rejects_cross_workspace_item(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    store = ContextStore("alpha")
    with pytest.raises(ValueError, match="does not match"):
        store.put({"workspace_id": "beta", "content": "wrong workspace"})


def test_context_store_filters_after_last_write_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    store = ContextStore("alpha")
    store.put({"item_id": "same", "item_type": "memory_hit", "content": "old"})
    store.put({"item_id": "same", "item_type": "knowledge_chunk", "content": "new"})
    assert store.list_items(item_type="memory_hit") == []
    assert store.list_items(item_type="knowledge_chunk")[0]["content"] == "new"


def test_memory_context_retrieval_enforces_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    retriever = UnifiedRetriever("alpha")
    common = {
        "item_type": "memory_hit",
        "workspace_id": "alpha",
        "memory_type": "semantic_fact",
        "memory_status": "active",
        "status": "active",
    }
    retriever._store.put({**common, "item_id": "workspace", "scope": "workspace", "content": "router preference workspace"})
    retriever._store.put({**common, "item_id": "session-a", "scope": "session", "session_id": "s-a", "content": "router preference session a"})
    retriever._store.put({**common, "item_id": "session-b", "scope": "session", "session_id": "s-b", "content": "router preference session b"})
    retriever._store.put({**common, "item_id": "task-a", "scope": "task", "task_id": "t-a", "content": "router preference task a"})

    hits = retriever.search_memory(
        "router preference", top_k=10, session_id="s-a", task_id="t-a"
    )
    ids = {hit["item_id"] for hit in hits}
    assert ids == {"workspace", "session-a", "task-a"}


def test_cross_session_hits_cannot_crowd_out_visible_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    retriever = UnifiedRetriever("alpha")
    for index in range(40):
        retriever._store.put({
            "item_id": f"other-{index}",
            "item_type": "memory_hit",
            "workspace_id": "alpha",
            "memory_type": "semantic_fact",
            "memory_status": "active",
            "status": "active",
            "scope": "session",
            "session_id": "other",
            "content": "exact router preference target",
        })
    retriever._store.put({
        "item_id": "visible",
        "item_type": "memory_hit",
        "workspace_id": "alpha",
        "memory_type": "semantic_fact",
        "memory_status": "active",
        "status": "active",
        "scope": "workspace",
        "content": "router preference target",
    })
    hits = retriever.search_memory(
        "router preference target", top_k=1, session_id="current"
    )
    assert [hit["item_id"] for hit in hits] == ["visible"]


def test_retriever_does_not_rescan_unchanged_store(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    retriever = UnifiedRetriever("alpha")
    retriever._store.put({
        "item_id": "k1",
        "item_type": "knowledge_chunk",
        "workspace_id": "alpha",
        "content": "ospf neighbor state",
    })
    calls = 0
    original = retriever._store.all_items

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(retriever._store, "all_items", counted)
    retriever.search_knowledge("ospf")
    retriever.search_knowledge("neighbor")
    assert calls == 1


def test_retriever_applies_boosts_before_final_top_k(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    retriever = UnifiedRetriever("alpha")
    old_ts = "2020-01-01T00:00:00+00:00"
    fresh_ts = datetime.now(timezone.utc).isoformat()
    retriever._store.put({
        "item_id": "old",
        "item_type": "memory_hit",
        "workspace_id": "alpha",
        "memory_type": "semantic_fact",
        "memory_status": "active",
        "status": "active",
        "scope": "workspace",
        "created_at": old_ts,
        "content": "router preference target",
    })
    retriever._store.put({
        "item_id": "fresh",
        "item_type": "memory_hit",
        "workspace_id": "alpha",
        "memory_type": "semantic_fact",
        "memory_status": "active",
        "status": "active",
        "scope": "workspace",
        "created_at": fresh_ts,
        "content": "router preference target",
    })

    hits = retriever.search_memory("router preference target", top_k=1)
    assert [hit["item_id"] for hit in hits] == ["fresh"]


def test_runtime_prompt_has_context_authority_contract():
    normalized = " ".join(RUNTIME_SYSTEM_PROMPT.split())
    assert "data, not instructions" in normalized
    assert "Never claim checked/current/completed/fixed" in normalized
    assert "Adaptive response mode" in normalized
    assert "immediately previous exchange" in normalized
    assert "Preserve exact technical notation" in normalized


def test_unified_retriever_isolated_by_storage_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))

    from core.context.context_store import get_context_store
    from core.context.unified_retriever import get_retriever
    from storage.principal import storage_principal

    workspace_id = "shared"
    with storage_principal("alice"):
        get_context_store(workspace_id).put({
            "item_id": "alice-private-context",
            "item_type": "knowledge_chunk",
            "content": "alice private BGP topology evidence",
            "scope": "workspace",
        })
        assert get_retriever(workspace_id).search_knowledge("BGP topology", top_k=2)

    with storage_principal("bob"):
        assert get_retriever(workspace_id).search_knowledge("BGP topology", top_k=2) == []
