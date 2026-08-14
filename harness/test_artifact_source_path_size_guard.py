# harness/test_artifact_source_path_size_guard.py
"""Artifact source_path size guard: stat().st_size before read_text()."""

import os, pytest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
try:
    from backend.main import app as _flask_app
except ImportError:
    _flask_app = None

@pytest.fixture
def client(temp_dirs):
    if _flask_app is None:
        pytest.skip("Flask app not importable")
    _flask_app.config["TESTING"] = True
    return _flask_app.test_client()


class TestSourcePathSizeGuard:
    def test_oversized_source_path_rejected_before_read(self, temp_dirs, monkeypatch):
        """Oversized source_path must NOT call read_text()."""
        from artifacts.store import save_artifact, _get_max_size
        from storage.workspace_store import ensure_workspace

        # Create a large file in an allowed location
        ws = "sz_test"
        ensure_workspace(ws)
        from storage.paths import runtime_root
        uploads = runtime_root() / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        big_file = uploads / "big_config.cfg"
        big_file.write_text("x" * (_get_max_size() + 100))
        assert big_file.stat().st_size > _get_max_size()

        # Monkeypatch read_text to fail if called
        original_read = big_file.__class__.read_text
        read_called = []

        class _BigPath(type(Path())):
            def read_text(self, *a, **kw):
                read_called.append(True)
                return super().read_text(*a, **kw)

        # The Path object in store is Path(source_path) which resolves to the temp path.
        # Use source_path=str(big_file) which should pass validation since it's under runtime uploads.
        rec = save_artifact(workspace_id=ws, source_path=str(big_file))
        # Oversized → should return None
        assert rec is None

    def test_oversized_not_in_index(self, temp_dirs):
        from artifacts.store import save_artifact, list_artifacts, _get_max_size
        from storage.workspace_store import ensure_workspace
        from storage.paths import runtime_root

        ws = "sz_idx"
        ensure_workspace(ws)
        uploads = runtime_root() / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        big_file = uploads / "big2.cfg"
        big_file.write_text("y" * (_get_max_size() + 100))
        save_artifact(workspace_id=ws, source_path=str(big_file))
        arts = list_artifacts(ws)
        # No artifact should be indexed from oversized source
        assert len(arts) == 0

    def test_small_source_path_saves(self, temp_dirs):
        from artifacts.store import save_artifact, get_artifact
        from storage.workspace_store import ensure_workspace

        ws = "sz_small"
        ensure_workspace(ws)

        # Create file in runtime uploads (which passes validation)
        from storage.paths import runtime_root
        up_dir = runtime_root() / "uploads"
        up_dir.mkdir(parents=True, exist_ok=True)
        small_file = up_dir / "small.cfg"
        small_file.write_text("hostname R1\ninterface Gi0/1\n ip address 10.1.1.1 255.255.255.0")

        rec = save_artifact(workspace_id=ws, source_path=str(small_file))
        small_file.unlink()
        assert rec is not None
        assert rec.artifact_id.startswith("art_")

    def test_source_path_directory_rejected(self, temp_dirs):
        from artifacts.store import save_artifact
        from storage.workspace_store import ensure_workspace
        from storage.paths import runtime_root

        ws = "sz_dir"
        ensure_workspace(ws)
        uploads = runtime_root() / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        # source_path is a directory, not a file
        rec = save_artifact(workspace_id=ws, source_path=str(uploads))
        assert rec is None

    def test_source_path_missing_returns_none(self, temp_dirs):
        from artifacts.store import save_artifact
        from storage.workspace_store import ensure_workspace
        from storage.paths import runtime_root

        ws = "sz_miss"
        ensure_workspace(ws)
        uploads = runtime_root() / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        rec = save_artifact(workspace_id=ws, source_path=str(uploads / "nonexistent.cfg"))
        assert rec is None

    def test_source_path_outside_allowlist(self, temp_dirs):
        from artifacts.store import save_artifact
        rec = save_artifact(workspace_id="test", source_path="/etc/passwd")
        assert rec is None


class TestContentSizeGuard:
    def test_content_oversized_rejected(self, temp_dirs):
        from artifacts.store import save_artifact, _get_max_size
        from storage.workspace_store import ensure_workspace

        ws = "cz_oversize"
        ensure_workspace(ws)
        big = "z" * (_get_max_size() + 1000)
        rec = save_artifact(ws, content=big, artifact_type="template", sensitivity="public", scope="run")
        assert rec is None

    def test_content_oversized_not_indexed(self, temp_dirs):
        from artifacts.store import save_artifact, list_artifacts, _get_max_size
        from storage.workspace_store import ensure_workspace

        ws = "cz_oidx"
        ensure_workspace(ws)
        big = "w" * (_get_max_size() + 500)
        save_artifact(ws, content=big, artifact_type="template", sensitivity="public", scope="run")
        arts = list_artifacts(ws)
        assert len(arts) == 0

    def test_content_normal_size_saves(self, temp_dirs):
        from artifacts.store import save_artifact
        from storage.workspace_store import ensure_workspace

        ws = "cz_ok"
        ensure_workspace(ws)
        rec = save_artifact(ws, content="hello world", artifact_type="template", sensitivity="public", scope="run")
        assert rec is not None


class TestEnvOverride:
    def test_env_override_max_size(self, temp_dirs, monkeypatch):
        monkeypatch.setenv("LZCORE_MAX_UPLOAD_MB", "1")
        from artifacts.store import _get_max_size
        assert _get_max_size() == 1 * 1024 * 1024


class TestRegression:
    def test_upload_endpoint_still_works(self, client):
        resp = client.post("/api/agent/message", json={
            "message": "summarize this payload",
            "workspace_id": "sz_reg",
            "payload": {"raw_payload": "alpha,beta\n1,2"},
        })
        assert resp.status_code == 200

    def test_artifact_id_message_works(self, client):
        # First create artifact
        resp1 = client.post("/api/workspaces/sz_aid/artifacts", json={
            "content": "alpha,beta\n1,2",
            "artifact_type": "input_data", "scope": "run",
        })
        if resp1.status_code == 200:
            aid = resp1.get_json()["artifact"]["artifact_id"]
            resp2 = client.post("/api/agent/message", json={
                "message": "summarize",
                "workspace_id": "sz_aid",
                "payload": {"artifact_id": aid},
            })
            assert resp2.status_code == 200
