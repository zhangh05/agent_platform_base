from storage.paths import user_data_root, user_runtime_root, workspace_root
from backend.core.identity import delete_user, ensure_identity_storage_ids, resolve_user_storage_id, upsert_user
from storage.principal import principal_storage_key, storage_principal
from storage.workspace_store import (
    delete_workspace,
    ensure_workspace,
    list_workspace_ids,
    list_workspaces,
    rename_workspace,
    update_workspace_state,
)


def test_configured_admin_uses_strict_user_and_workspace_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_PLATFORM_LOGIN_USERNAME", "Admin")
    key = principal_storage_key("Admin")

    with storage_principal("Admin"):
        assert workspace_root("default") == tmp_path / "users" / key / "workspaces" / "default"
        assert user_runtime_root() == tmp_path / "users" / key / "runtime"


def test_workspace_catalog_is_separate_from_user_data(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    with storage_principal("Admin"):
        ensure_workspace("default")
    assert list_workspace_ids() == ["default"]
    assert (tmp_path / "catalog" / "default").is_dir()
    assert (tmp_path / "users" / principal_storage_key("Admin") / "workspaces" / "default").is_dir()


def test_workspace_control_metadata_is_written_to_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    with storage_principal("Admin"):
        update_workspace_state("default", {"organization_id": "network", "owner_username": "Admin"})
        listed = list_workspaces()
    assert listed[0]["organization_id"] == "network"
    assert listed[0]["owner_username"] == "Admin"


def test_workspace_rename_and_delete_cover_every_user_data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    for username in ("Admin", "network"):
        with storage_principal(username):
            ensure_workspace("team")
            (workspace_root("team") / "marker.txt").write_text(username, encoding="utf-8")

    assert rename_workspace("team", "team_new")["ok"] is True
    for username in ("Admin", "network"):
        key = principal_storage_key(username)
        assert (tmp_path / "users" / key / "workspaces" / "team_new" / "marker.txt").is_file()

    assert delete_workspace("team_new")["ok"] is True
    assert not (tmp_path / "catalog" / "team_new").exists()
    for username in ("Admin", "network"):
        key = principal_storage_key(username)
        assert not (tmp_path / "users" / key / "workspaces" / "team_new").exists()


def test_user_creation_eagerly_provisions_immutable_root_and_delete_removes_it(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    user = upsert_user("alice", "password", "viewer", "org", ["team"], home_workspace_id="team")

    user_id = resolve_user_storage_id("alice")
    root = user_data_root(user_id)
    assert user["username"] == "alice"
    assert (root / "profile.json").is_file()
    assert (root / "workspaces" / "team" / "sessions").is_dir()

    delete_user("alice")
    assert not root.exists()


def test_workspace_objects_and_approval_audit_follow_user_workspace_root(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    upsert_user("alice", "password", "viewer", "org", ["team"])
    upsert_user("bob", "password", "viewer", "org_b", ["team_b"])
    from storage.approval_record_store import approval_log_path
    from storage.object_store import get_object_store

    with storage_principal("alice"):
        objects = get_object_store("team")
        objects.put("uploads/example.bin", b"alice")
        assert objects.root == workspace_root("team") / "objects"
        assert approval_log_path("team") == workspace_root("team") / "approvals" / "tool_approvals.jsonl"

    with storage_principal("bob"):
        assert get_object_store("team_b").get("uploads/example.bin") is None


def test_identity_upgrade_assigns_immutable_ids_without_preserving_legacy_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    from storage.atomic_io import atomic_write_json
    from storage.records import runtime_record_file

    atomic_write_json(runtime_record_file("identity", "users.json"), {
        "users": [{"username": "legacy", "password_hash": "x", "role": "viewer"}],
        "organizations": [], "memberships": [],
    })
    assert ensure_identity_storage_ids() == 1
    user_id = resolve_user_storage_id("legacy")
    assert user_id.startswith("usr_") and len(user_id) == 36
    assert not (tmp_path / "users" / "legacy-old-root").exists()
