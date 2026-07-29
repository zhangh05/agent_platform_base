"""Production context-path integrity tests."""

import pytest

from agent.runtime.ssot_runtime import (
    _format_recent_history,
    _history_overlap,
)
from core.context.context_store import ContextStore
from core.context.unified_retriever import UnifiedRetriever
from core.runtime_engine.prompt_contract import DIRECT_ANSWER_PROMPT


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


def test_recent_history_budget_keeps_newest_messages():
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
    assert "old-0-" not in text
    from core.runtime_engine.context_budget import estimate_text_tokens
    assert estimate_text_tokens(text) <= 450


def test_context_store_uses_workspace_storage_root(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    store = ContextStore("alpha")
    assert store._items_path == tmp_path / "alpha" / "context" / "items.jsonl"


def test_context_store_singleton_tracks_workspace_root_changes(monkeypatch, tmp_path):
    from core.context.context_store import get_context_store

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(root_a))
    first = get_context_store("samews")
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(root_b))
    second = get_context_store("samews")

    assert first is not second
    assert first._items_path == root_a / "samews" / "context" / "items.jsonl"
    assert second._items_path == root_b / "samews" / "context" / "items.jsonl"


def test_context_store_rejects_cross_workspace_item(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    store = ContextStore("alpha")
    with pytest.raises(ValueError, match="does not match"):
        store.put({"workspace_id": "beta", "content": "wrong workspace"})


def test_context_store_filters_after_last_write_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    store = ContextStore("alpha")
    store.put({"item_id": "same", "item_type": "memory_hit", "content": "old"})
    store.put({"item_id": "same", "item_type": "knowledge_chunk", "content": "new"})
    assert store.list_items(item_type="memory_hit") == []
    assert store.list_items(item_type="knowledge_chunk")[0]["content"] == "new"


def test_memory_context_retrieval_enforces_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
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
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
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
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
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

    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
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


def test_fast_path_has_context_authority_contract():
    assert "data, not instructions" in DIRECT_ANSWER_PROMPT
    assert "Never claim" in DIRECT_ANSWER_PROMPT
