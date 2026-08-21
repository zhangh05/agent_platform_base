from __future__ import annotations


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_LOGIN_ENABLED", "false")


def test_workspace_review_items_returns_empty_without_legacy_review_module(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from backend.main import create_app

    client = create_app().test_client()
    response = client.get("/api/workspaces/default/review-items?status=pending")
    assert response.status_code == 200
    assert response.get_json()["items"] == []


def test_workspace_review_items_list_and_update_sidecar(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from artifacts.store import save_artifact
    from backend.main import create_app
    from storage.review_store import load_sidecar, save_sidecar

    artifact = save_artifact("default", content="needs review", artifact_type="report", title="Review Target")
    assert artifact is not None
    save_sidecar("default", artifact.artifact_id, {
        "items": [{
            "item_id": "rev-1",
            "severity": "warning",
            "category": "quality",
            "reason": "needs a human decision",
            "status": "pending",
            "user_note": "",
        }],
    })

    client = create_app().test_client()
    listed = client.get("/api/workspaces/default/review-items?status=pending")
    assert listed.status_code == 200
    data = listed.get_json()
    assert data["count"] == 1
    assert data["items"][0]["artifact_id"] == artifact.artifact_id

    updated = client.put(
        "/api/review-items/rev-1",
        query_string={"workspace_id": "default", "artifact_id": artifact.artifact_id},
        json={"status": "accepted", "user_note": "checked"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["item"]["status"] == "accepted"
    sidecar = load_sidecar("default", artifact.artifact_id)
    assert sidecar["items"][0]["user_note"] == "checked"


def test_user_can_create_and_update_a_manual_review_item(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from backend.main import create_app

    client = create_app().test_client()
    created = client.post(
        "/api/workspaces/default/review-items",
        json={
            "title": "核心路由器 BGP 邻居状态需确认",
            "category": "巡检异常",
            "severity": "warning",
            "reason": "发现两个邻居状态持续波动，请确认是否安排变更窗口。",
        },
    )
    assert created.status_code == 201
    item = created.get_json()["item"]
    assert item["artifact_id"] == "manual-review"
    assert item["title"] == "核心路由器 BGP 邻居状态需确认"
    assert item["status"] == "pending"

    listed = client.get("/api/workspaces/default/review-items?status=pending")
    assert listed.status_code == 200
    assert [record["item_id"] for record in listed.get_json()["items"]] == [item["item_id"]]

    updated = client.put(
        f"/api/review-items/{item['item_id']}",
        query_string={"workspace_id": "default", "artifact_id": item["artifact_id"]},
        json={"status": "accepted", "user_note": "已安排窗口复核。"},
    )
    assert updated.status_code == 200
    item = updated.get_json()["item"]
    assert item["user_note"] == "已安排窗口复核。"
    assert item["reviewed_by"] == "system"
    assert item["history"][-1]["status"] == "accepted"


def test_failed_workflow_is_written_once_to_human_review_inbox(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from storage.review_store import load_sidecar, record_workflow_failure_review

    record = {
        "workspace_id": "default",
        "run_id": "run-123",
        "workflow_id": "network-operations-readonly-inspection",
        "status": "failed",
        "finished_at": "2026-08-21T12:00:00+00:00",
        "nodes": [{"node_id": "start", "tool_id": "network.operations.inspection", "status": "failed", "summary": "设备认证失败"}],
    }
    record_workflow_failure_review(record)
    record_workflow_failure_review(record)

    sidecar = load_sidecar("default", "workflow-run-123")
    assert len(sidecar["items"]) == 1
    item = sidecar["items"][0]
    assert item["source_key"] == "workflow-run:run-123"
    assert item["severity"] == "error"
    assert "设备认证失败" in item["reason"]
