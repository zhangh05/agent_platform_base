from storage.paths import user_runtime_root, workspace_root
from storage.principal import principal_storage_key, storage_principal


def test_configured_admin_uses_strict_user_and_workspace_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("NA_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("AGENT_PLATFORM_LOGIN_USERNAME", "Admin")
    key = principal_storage_key("Admin")

    with storage_principal("Admin"):
        assert workspace_root("default") == tmp_path / "default" / "users" / key
        assert user_runtime_root() == tmp_path / "_runtime" / "users" / key
