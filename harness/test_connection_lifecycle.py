"""Isolated regressions for identity, endpoint transactions and late network IO."""
from contextvars import ContextVar
import threading

import pytest

from extensions.network_operations import service
from storage.principal import ContextThreadPoolExecutor, current_storage_principal, storage_principal


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_MASTER_KEY", "lifecycle-test-key")
    monkeypatch.setattr(service, "resolve_source_address", lambda *_: "")


def register(name="CE1", port=30001):
    device = service.save_device("default", {"name": name, "host": "10.0.0.1", "vendor": "h3c"})
    connection = service.save_connection("default", {
        "device_id": device["device_id"], "protocol": "telnet", "port": port, "auth_method": "none",
    }, auto_test=False)
    return device, connection


@pytest.mark.parametrize("mode", ["parallel_read", "inspection"])
@pytest.mark.parametrize("one_offline", [False, True])
def test_six_authorized_devices_only_two_explicit_targets_are_contacted(monkeypatch, mode, one_offline):
    from types import SimpleNamespace
    from extensions.network_operations.backend import device_manage, inspection
    pairs = [register(f"CE{i}", 30001+i) for i in range(6)]
    ids = [c["connection_id"] for _, c in pairs]
    skill = service.save_skill("default", {"name": "six", "device_ids": [d["device_id"] for d, _ in pairs], "connection_ids": ids})
    seen = []
    def probe(target, **kwargs):
        seen.append((target.port, kwargs.get("commands")))
        if one_offline and target.port == 30002:
            return {"ok": False, "error": "offline"}
        return {"ok": True, "read_ok": True, "output": {"display cur": "sysname CE"},
                "command_results": [{"command": "display cur", "complete": True}]}
    monkeypatch.setattr(service, "probe_target", probe)
    resolved = service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"]})
    assert seen == []
    def invocation(arguments):
        return SimpleNamespace(workspace_id="default", skill=skill["skill_id"],
                               skill_connection_ids=tuple(resolved["connection_ids"]), arguments=arguments)
    if mode == "parallel_read":
        with ContextThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda cid: device_manage(invocation({"action": "read", "connection_id": cid, "commands": ["display cur"]})), ids[:2]))
        assert results[0]["connection_ok"] is True
        assert results[1]["connection_ok"] is (not one_offline)
        if one_offline:
            assert results[1]["decision_required"] is True
    else:
        task = inspection(invocation({"action": "run", "connection_ids": ids[:2], "commands": ["display cur"]}))["task"]
        from jobs.runner import run_job
        stored = service.get_inspection("default", task["task_id"])
        run_job("default", stored["job_id"])
        finished = service.get_inspection("default", task["task_id"])
        assert set(finished["results"]) == set(ids[:2])
        assert finished["status"] == ("partial" if one_offline else "succeeded")
    assert sorted(seen) == [(30001, ["display cur"]), (30002, ["display cur"])]
    assert all(service.get_connection("default", cid)["status"] == "untested" for cid in ids[2:])


def test_pool_captures_each_submission_and_never_leaks_reused_worker_context():
    marker = ContextVar("invocation_marker", default="unset")
    def observe(_=None):
        result = current_storage_principal(), marker.get()
        marker.set("worker-mutation")
        return result
    with ContextThreadPoolExecutor(max_workers=1) as pool:
        for user in ("alice", "bob", ""):
            with storage_principal(user):
                token = marker.set(user + "-run")
                try:
                    assert pool.submit(observe).result() == (user, user + "-run")
                    assert list(pool.map(observe, range(3))) == [(user, user + "-run")] * 3
                    assert marker.get() == user + "-run"
                finally:
                    marker.reset(token)
        def fail():
            marker.set("failed-worker")
            raise RuntimeError("expected")
        with pytest.raises(RuntimeError, match="expected"):
            pool.submit(fail).result()
        assert pool.submit(observe).result()[1] == "unset"


@pytest.mark.parametrize("mutation", ["delete_connection", "delete_device", "edit_port", "edit_source", "edit_host", "newer_probe"])
def test_probe_never_resurrects_or_overwrites_newer_state(monkeypatch, mutation):
    with storage_principal("alice"):
        device, connection = register()
        cid = connection["connection_id"]
        skill = service.save_skill("default", {
            "name": "test", "device_ids": [device["device_id"]], "connection_ids": [cid],
        })
        entered, release = threading.Event(), threading.Event()
        def slow_probe(*_args, **_kwargs):
            entered.set()
            assert release.wait(5), "management operation blocked on network IO"
            return {"ok": True, "duration_ms": 99}
        monkeypatch.setattr(service, "probe_target", slow_probe)
        with ContextThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(service.test_connection, "default", cid)
            try:
                assert entered.wait(5)
                if mutation == "delete_connection":
                    assert service.delete_connection("default", cid)
                elif mutation == "delete_device":
                    assert service.delete_device("default", device["device_id"])
                elif mutation == "edit_host":
                    service.save_device("default", {**device, "host": "10.0.0.2"})
                elif mutation == "newer_probe":
                    monkeypatch.setattr(service, "probe_target", lambda *_a, **_k: {"ok": False, "error": "newest_failure"})
                    # The next invocation can own the observation while its
                    # network IO waits for this endpoint's execution lock.
                    first_probe = service.get_connection("default", cid, include_secret=True)["probe_id"]
                    newer = pool.submit(service.test_connection, "default", cid)
                    import time
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if service.get_connection("default", cid, include_secret=True)["probe_id"] != first_probe:
                            break
                        time.sleep(0.01)
                else:
                    changes = {"port": 30002} if mutation == "edit_port" else {"source_address": "100.64.0.1"}
                    service.save_connection("default", {**connection, **changes}, auto_test=False)
            finally:
                release.set()
            stale = pending.result(timeout=5)
            if mutation == "newer_probe":
                assert not newer.result(timeout=5)["ok"]
        assert stale["ok"] is (mutation == "newer_probe")
        current = service.get_connection("default", cid)
        if mutation.startswith("delete"):
            assert stale["error"] == "connection_deleted_during_test"
            assert current is None
            assert service.get_skill("default", skill["skill_id"]) is None
            with pytest.raises(ValueError, match="connection_not_found|device_not_found"):
                service.save_connection("default", connection, auto_test=False)
        else:
            if mutation == "newer_probe":
                assert stale["observation_superseded"] is True
            else:
                assert stale["error"] == "connection_changed_during_test"
            assert current["status"] == ("failed" if mutation == "newer_probe" else "untested")
            if mutation == "edit_port":
                assert current["port"] == 30002
            elif mutation == "edit_source":
                assert current["source_address"] == "100.64.0.1"
            elif mutation == "newer_probe":
                assert current["last_error"] == "newest_failure"


def test_on_demand_semantic_inspection_uses_authenticated_storage(monkeypatch):
    seen = []
    def probe(target, **_kwargs):
        seen.append(current_storage_principal())
        return {"ok": True, "read_ok": True, "output": {"display version": "H3C"},
                "command_results": [{"command": "display version", "complete": True}]}
    monkeypatch.setattr(service, "probe_target", probe)
    with storage_principal("alice"):
        pairs = [register("CE1"), register("CE2", 30002)]
        ids = [connection["connection_id"] for _, connection in pairs]
        skill = service.save_skill("default", {"name": "pair", "device_ids": [d["device_id"] for d, _ in pairs], "connection_ids": ids})
        result = service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"]})
        assert set(result["connection_ids"]) == set(ids)
        assert seen == []
        task = service.enqueue_connection_inspection("default", ids, facts=["device_version"])
        from jobs.runner import run_job
        run_job("default", task["job_id"])
        finished = service.get_inspection("default", task["task_id"])
        assert finished["status"] == "succeeded"
        assert finished["completed"] == 2
    assert seen == ["alice"] * 2
    with storage_principal("bob"):
        assert service.list_connections("default") == []
        assert service.list_inspections("default") == []


def test_endpoint_conflict_rejects_edit_without_destroying_credentials():
    device, first = register()
    second = service.save_connection("default", {
        "device_id": device["device_id"], "protocol": "telnet", "port": 30002,
        "auth_method": "password", "username": "ops", "password": "keep-secret",
    }, auto_test=False)
    original = service.get_connection("default", second["connection_id"], include_secret=True)
    with pytest.raises(ValueError, match="connection_endpoint_already_exists"):
        service.save_connection("default", {**second, "port": first["port"], "auth_method": "none"}, auto_test=False)
    assert service.get_connection("default", second["connection_id"], include_secret=True) == original
    assert service.ExtensionSecretStore.get(original["password_ref"]) == "keep-secret"


def test_invalid_auth_change_is_side_effect_free():
    device, _ = register()
    connection = service.save_connection("default", {
        "device_id": device["device_id"], "protocol": "ssh", "username": "ops", "password": "original",
    }, auto_test=False)
    original = service.get_connection("default", connection["connection_id"], include_secret=True)
    with pytest.raises(ValueError, match="private key is required"):
        service.save_connection("default", {**connection, "auth_method": "private_key", "password": "must-not-write"}, auto_test=False)
    assert service.get_connection("default", connection["connection_id"], include_secret=True) == original
    assert service.ExtensionSecretStore.get(original["password_ref"]) == "original"


def test_explicit_migration_preserves_shared_credentials_and_updates_skill_refs(monkeypatch):
    with storage_principal("alice"):
        device, _ = register()
        connection = service.save_connection("default", {
            "device_id": device["device_id"], "protocol": "ssh", "username": "ops", "password": "shared-secret",
        }, auto_test=False)
        original = service.get_connection("default", connection["connection_id"], include_secret=True)
        duplicate = {**original, "connection_id": "old_duplicate", "updated_at": "2099-01-01T00:00:00+00:00"}
        service._store("default").save("connections", "old_duplicate", duplicate)
        skill = service.save_skill("default", {
            "name": "legacy", "device_ids": [device["device_id"]],
            "connection_ids": [original["connection_id"], "old_duplicate"],
        })
        before = service._raw_connections("default")
        service.list_connections("default")
        service.workbench_skill_catalog("default")
        assert service._raw_connections("default") == before
        assert service.get_skill("default", skill["skill_id"])["connection_ids"] == skill["connection_ids"]
        assert service.reconcile_duplicate_connections("default") == 1
        assert service.reconcile_duplicate_connections("default") == 0
        assert service.ExtensionSecretStore.get(original["password_ref"]) == "shared-secret"
        monkeypatch.setattr(service, "probe_target", lambda *_a, **_k: {"ok": True})
        result = service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"]})
        assert len(result["connection_ids"]) == 1
        assert service.delete_connection("default", result["connection_ids"][0])
        assert not service.ExtensionSecretStore.get(original["password_ref"])


@pytest.mark.parametrize("width", [1, 2, 5])
def test_workflow_reads_same_principal_in_serial_and_parallel(width):
    from workflows.service import save_workflow, execute_workflow
    from extensions.runtime import reset_extension_cache_for_tests
    from core.tools.integration import reset_default_client_for_tests
    reset_extension_cache_for_tests()
    reset_default_client_for_tests()
    with storage_principal("alice"):
        device, _ = register()
        save_workflow("default", {
            "workflow_id": "context_graph", "name": "context graph",
            "nodes": [{"node_id": f"read_{i}", "tool_id": "network.operations.devices_read", "arguments": {}} for i in range(width)],
        })
        result = execute_workflow("default", "context_graph")
        assert result["status"] == "succeeded"
        assert len(result["nodes"]) == width
        for node in result["nodes"]:
            assert node["output"]["devices"][0]["device_id"] == device["device_id"]


def test_retired_writers_and_in_memory_worker_are_absent():
    for name in (
        "save_asset", "delete_asset", "probe_asset", "start_inspection", "_TASK_CANCEL",
        "update_finding_state", "save_inspection_schedule", "run_due_inspection_schedules",
        "create_baseline", "confirm_baseline", "diff_against_current", "overview",
    ):
        assert not hasattr(service, name), name


def test_historical_asset_task_can_still_retry_through_durable_worker(monkeypatch):
    from jobs.runner import run_job
    with storage_principal("alice"):
        asset = {"asset_id": "historical_router", "name": "old-router", "host": "10.0.0.2", "vendor": "h3c"}
        service._store("default").save("assets", asset["asset_id"], asset)
        task = service._build_inspection_task([asset], ["display version"], None)
        task["status"] = "failed"
        service._store("default").save("inspections", task["task_id"], task)
        def collect(_target, commands, **_kwargs):
            assert current_storage_principal() == "alice"
            return {"ok": True, "read_ok": True,
                    "output": {command: "historical-evidence" for command in commands},
                    "command_results": [{"command": command, "complete": True} for command in commands]}
        monkeypatch.setattr(service, "collect_connection", collect)
        retried = service.retry_inspection("default", task["task_id"])
        run_job("default", retried["job_id"])
        assert service.get_inspection("default", retried["task_id"])["status"] == "succeeded"
        evidence = service.inspection_evidence_summary("default", retried["task_id"])
        assert evidence["devices"][0]["asset_id"] == "historical_router"
        assert service.get_inspection("default", task["task_id"])["status"] == "failed"


def test_startup_migration_visits_each_principal_without_query_side_effects(monkeypatch):
    monkeypatch.setattr("storage.principal.known_storage_principals", lambda: ["alice", "bob"])
    monkeypatch.setattr("backend.core.identity.get_user", lambda _user: {"workspace_ids": ["default"]})
    for user in ("alice", "bob"):
        with storage_principal(user):
            _, connection = register()
            original = service.get_connection("default", connection["connection_id"], include_secret=True)
            service._store("default").save("connections", "old_duplicate", {**original, "connection_id": "old_duplicate"})
            assert len(service.list_connections("default")) == 2
    assert service.reconcile_network_state() == 0
    assert service.reconcile_network_state() == 0
    for user in ("alice", "bob"):
        with storage_principal(user):
            assert len(service.list_connections("default")) == 1
