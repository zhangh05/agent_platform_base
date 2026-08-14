from pathlib import Path
import io
import json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_current_knowledge_surfaces_exist():
    current_paths = [
        "agent/modules/knowledge",
        "core/context/unified_retriever.py",
        "backend/api/knowledge_routes.py",
    ]
    for path in current_paths:
        assert (PROJECT_ROOT / path).exists(), f"{path} should exist"


def test_knowledge_search_rejects_unknown_query_params():
    from backend.main import app

    client = app.test_client()
    resp = client.get(
        "/api/knowledge/search",
        query_string={"workspace_id": "default", "q": "ospf", "unsupported": "value"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "invalid_query_params"
    assert body["invalid_params"] == ["unsupported"]


def test_knowledge_upload_writes_through_filestore(monkeypatch, tmp_path):
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("LZCORE_WORKSPACE_DIR", str(workspace_root))

    from backend.main import app
    from storage.file_store import list_files

    client = app.test_client()
    resp = client.post(
        "/api/knowledge/upload",
        data={
            "workspace_id": "default",
            "file": (io.BytesIO(b"# OSPF\nneighbor state"), "ospf.md"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    files = list_files("default")
    assert len(files) == 1
    normalized = files[0]
    assert normalized["logical_type"] == "knowledge_normalized"
    assert normalized["path"].startswith("files/data/ksrc_")
    assert normalized["path"].endswith(".md")
    assert normalized["metadata"]["normalized_format"] == "markdown"

    source_id = body["source"]["source_id"]
    listed = client.get(
        "/api/knowledge/sources",
        query_string={"workspace_id": "default", "scope": "workspace"},
    ).get_json()
    assert [source["source_id"] for source in listed["sources"]] == [source_id]
    global_list = client.get(
        "/api/knowledge/sources",
        query_string={"workspace_id": "default", "scope": "global"},
    ).get_json()
    assert global_list["sources"] == []

    disabled = client.patch(
        f"/api/knowledge/sources/{source_id}",
        json={"workspace_id": "default", "enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.get_json()["source"]["enabled"] is False

    deleted = client.delete(
        f"/api/knowledge/sources/{source_id}",
        query_string={"workspace_id": "default"},
    )
    assert deleted.status_code == 200
    assert client.delete(
        f"/api/knowledge/sources/{source_id}",
        query_string={"workspace_id": "default"},
    ).status_code == 404
    assert list_files("default") == []
    assert not (workspace_root / "default" / normalized["path"]).exists()


def test_frontend_knowledge_search_uses_current_query_contract():
    text = _read("frontend/src/api/index.ts")
    search_block = text.split("search: (", 1)[1].split("getChunk:", 1)[0]
    assert "workspace_id" in search_block
    assert "q:" in search_block
    assert "limit" in search_block


def test_knowledge_events_broadcast_to_all_workspace_subscribers():
    from storage.events import publish, subscribe

    with subscribe("default") as first, subscribe("default") as second:
        publish("default", "knowledge", "updated", "ksrc_0123456789ab")
        first_event = json.loads(first.get_nowait())
        second_event = json.loads(second.get_nowait())

    assert first_event == second_event
    assert first_event["domain"] == "knowledge"
    assert first_event["action"] == "updated"
    assert first_event["entity_id"] == "ksrc_0123456789ab"


def test_llm_tool_catalog_exposes_current_knowledge_search():
    # Sub-agent dispatch is handled by the durable subagent runtime plus
    # ``agent.manage``. Keep the canonical registry assertion focused on
    # the current modules only.
    targets = [
        "core/tools/canonical_registry.py",
        "core/tools/tool_namespace_data.py",
        "agent/capabilities/catalog.py",
    ]
    for target in targets:
        text = _read(target)
        assert "knowledge.manage" in text
    # Verify the SSOT Runtime-era sub-agent path. The trust marker
    # ``is_sub_agent`` lives on ``AgentSession`` and is written
    # by ``agent.runtime.durable.subagent`` (which calls
    # ``sess.mark_sub_agent()`` on the child session). The
    # adapter itself does not need the marker — it runs whatever
    # session it is given — so we read it from the durable
    # dispatcher instead.
    durable_src = _read("agent/runtime/durable/subagent.py")
    assert "mark_sub_agent()" in durable_src
    # The marker is owned by ``AgentSession.is_sub_agent``;
    # the durable dispatcher is the legitimate caller.
    from agent.core.session import AgentSession
    s = AgentSession(session_id="spec-knowledge-cat-1", workspace_id="default")
    s.mark_sub_agent()
    assert s.is_sub_agent is True
    adapter_src = _read("agent/runtime/ssot_runtime.py")
    assert "from agent.runtime.sub_agent" not in adapter_src, (
        "ssot_runtime must not import removed subagent modules."
    )


def test_import_from_artifact_uses_current_store_and_is_searchable(tmp_path, monkeypatch):
    from artifacts import store as artifact_store
    from artifacts.store import save_artifact
    from backend.main import app

    ws_root = tmp_path / "workspaces"
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(ws_root))

    rec = save_artifact(
        "latest_knowledge_ws",
        content="OSPF neighbor jitter runbook: check one-way hello and MTU mismatch.",
        artifact_type="knowledge_doc",
        title="OSPF Jitter Runbook",
        sensitivity="internal",
        scope="workspace",
    )

    client = app.test_client()
    import_resp = client.post(
        "/api/knowledge/sources/from-artifact",
        json={"workspace_id": "latest_knowledge_ws", "artifact_id": rec.artifact_id},
    )
    assert import_resp.status_code == 200
    imported = import_resp.get_json()
    assert imported["source"]["source_id"].startswith("ksrc_")

    search_resp = client.get(
        "/api/knowledge/search",
        query_string={"workspace_id": "latest_knowledge_ws", "q": "OSPF neighbor"},
    )
    assert search_resp.status_code == 200
    body = search_resp.get_json()
    assert body["count"] >= 1
    assert "OSPF" in body["results"][0]["safe_excerpt"]
    assert body["results"][0]["artifact_id"] == rec.artifact_id
