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
