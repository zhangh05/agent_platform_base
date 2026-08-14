from __future__ import annotations

import json
from pathlib import Path
import tarfile

import pytest

from core.runtime.backup import BackupError, create_backup, restore_backup, verify_backup
from deployment.slots import ReleaseError, activate_release, rollback_release, stage_release
from jobs.queue import RedisJobQueue


class FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def lpush(self, name, value): self.lists.setdefault(name, []).insert(0, value)
    def rpoplpush(self, source, destination):
        if not self.lists.get(source): return None
        value = self.lists[source].pop()
        self.lists.setdefault(destination, []).insert(0, value)
        return value
    def lrem(self, name, _count, value):
        before = len(self.lists.get(name, []))
        self.lists[name] = [item for item in self.lists.get(name, []) if item != value]
        return before - len(self.lists[name])
    def hset(self, name, key, value): self.hashes.setdefault(name, {})[key] = value
    def hdel(self, name, key): self.hashes.setdefault(name, {}).pop(key, None)
    def hgetall(self, name): return dict(self.hashes.get(name, {}))
    def hexists(self, name, key): return key in self.hashes.get(name, {})
    def ping(self): return True


def test_redis_queue_renews_and_reclaims_stale_leases():
    queue = RedisJobQueue.__new__(RedisJobQueue)
    queue.client = FakeRedis()
    queue.enqueue("default", "job_12345678")
    receipt = queue.claim("worker-a")
    assert receipt and queue.heartbeat(receipt, "worker-a") is True
    queue.client.hashes[queue.LEASES][receipt.lease_id] = json.dumps({"worker_id": "worker-a", "heartbeat_at": 0})
    assert queue.reclaim_stale(30) == 1
    retried = queue.claim("worker-b")
    assert retried and retried.attempt == 2
    queue.ack(retried)
    assert queue.client.lists[queue.PROCESSING] == []


def test_backup_verify_restore_and_traversal_rejection(monkeypatch, tmp_path: Path):
    workspace_root = tmp_path / "workspaces"
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("LZCORE_BACKUP_DIR", str(backup_dir))
    source = workspace_root / "default" / "runs" / "run.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"status":"original"}', encoding="utf-8")
    backup = create_backup()
    assert verify_backup(backup["path"])["file_count"] == 1
    source.write_text('{"status":"changed"}', encoding="utf-8")
    with pytest.raises(BackupError, match="confirmation"):
        restore_backup(backup["path"], confirmation="")
    restored = restore_backup(backup["path"], confirmation="RESTORE")
    assert json.loads(source.read_text(encoding="utf-8"))["status"] == "original"
    assert Path(restored["rollback_path"]).is_dir()

    unsafe = tmp_path / "unsafe.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("unsafe", encoding="utf-8")
    with tarfile.open(unsafe, "w:gz") as archive:
        archive.add(payload, arcname="../escape")
    with pytest.raises(BackupError, match="unsafe backup path"):
        verify_backup(unsafe)


def _release_source(root: Path, version: str) -> Path:
    source = root / version
    (source / "agent").mkdir(parents=True)
    (source / "frontend" / "dist").mkdir(parents=True)
    (source / "agent" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (source / "frontend" / "dist" / "index.html").write_text(version, encoding="utf-8")
    return source


def test_release_slots_activate_and_rollback(tmp_path: Path):
    root = tmp_path / "slots"
    first = stage_release(_release_source(tmp_path / "sources", "1.4.0"), "1.4.0", root=root)
    stage_release(_release_source(tmp_path / "sources", "1.4.1"), "1.4.1", root=root)
    assert Path(first["path"], "release.json").is_file()
    activate_release("1.4.0", root=root)
    activate_release("1.4.1", root=root)
    assert (root / "current").resolve().name == "1.4.1"
    rolled_back = rollback_release(root=root)
    assert rolled_back["version"] == "1.4.0"
    assert (root / "current").resolve().name == "1.4.0"

    linked = _release_source(tmp_path / "sources", "1.4.2")
    (linked / "leaked-key").symlink_to(tmp_path / "outside-key")
    with pytest.raises(ReleaseError, match="symbolic link"):
        stage_release(linked, "1.4.2", root=root)
    nested = _release_source(tmp_path / "nested", "1.4.3")
    with pytest.raises(ReleaseError, match="outside"):
        stage_release(nested, "1.4.3", root=nested / "release-slots")


def test_readiness_and_bounded_http_metrics(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from backend.main import create_app
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    ready = client.get("/api/ready")
    assert ready.status_code == 200
    assert ready.get_json()["ready"] is True
    client.get("/api/health")
    metrics = client.get("/api/metrics").get_json()
    assert any(item["route"] == "/api/health" for item in metrics["requests"])
    prometheus = client.get("/metrics").get_data(as_text=True)
    assert "lzcore_http_requests_total" in prometheus

    from observability.metrics import record_operation, set_operational_gauge, render_prometheus
    record_operation("tool", "failed")
    set_operational_gauge("approval_pending", 2)
    rendered = render_prometheus()
    assert 'lzcore_operations_total{operation="tool",status="failed"}' in rendered
    assert 'lzcore_operational_gauge{name="approval_pending"} 2.0' in rendered


def test_metrics_require_auth_when_api_auth_is_enabled(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_AUTH_ENABLED", "true")
    monkeypatch.setenv("LZCORE_API_TOKEN", "metrics-token")
    from backend.main import create_app
    client = create_app().test_client()
    assert client.get("/api/health").status_code == 200
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer metrics-token"}).status_code == 200


def test_record_and_object_stores_can_be_configured_independently(monkeypatch):
    monkeypatch.setenv("LZCORE_RECORD_STORE_MODE", "postgres")
    monkeypatch.setenv("LZCORE_OBJECT_STORE_MODE", "s3")
    monkeypatch.setenv("LZCORE_DATABASE_URL", "postgresql://example.invalid/platform")
    monkeypatch.setenv("LZCORE_OBJECT_STORE_BUCKET", "platform-artifacts")
    from storage.backend import backend_mode, validate_backend_configuration
    from storage.object_store import object_store_mode
    assert backend_mode() == "postgres"
    assert object_store_mode() == "s3"
    assert validate_backend_configuration() == []
