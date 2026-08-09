from storage.paths import user_runtime_root, workspace_root
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
        assert user_runtime_root() == tmp_path / "_runtime" / "users" / key


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
