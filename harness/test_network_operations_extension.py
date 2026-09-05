from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from extensions.network_operations import service
from extensions.network_operations.backend import (
    device_manage,
    devices_read,
    inspection,
    skills_read,
)
from extensions.network_operations.device_tools import (
    MAX_READ_ONLY_COMMANDS,
    is_read_only_command as device_is_read_only_command,
    normalize_read_only_commands,
    resolve_source_address,
)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_MASTER_KEY", "test-extension-master-key")


def test_configuration_capability_is_opt_in_validated_and_revocable(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    conn = _register_connection("default", {"name": "CE", "host": "127.0.0.1", "protocol": "telnet", "vendor": "h3c"})
    payload = {"name": "test", "device_ids": [conn["device_id"]], "connection_ids": [conn["connection_id"]]}
    skill = service.save_skill("default", payload)
    assert skill["capabilities"] == []
    inv = SimpleNamespace(workspace_id="default", skill=skill["skill_id"], skill_connection_ids=(conn["connection_id"],),
                          arguments={"action": "configure", "connection_id": conn["connection_id"], "commands": ["system-view", "return"]})
    calls = []
    monkeypatch.setattr(service, "probe_target", lambda *a, **kw: calls.append(kw) or {"ok": True, "configuration_ok": True})
    assert device_manage(inv)["error"] == "configuration_not_allowed_by_skill"
    assert not calls
    skill = service.save_skill("default", {**skill, "capabilities": ["configuration_write"]})
    snapshot = service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"]})
    assert snapshot["capabilities"] == ["configuration_write"]
    assert device_manage(inv)["configuration_ok"]
    assert calls[0]["configure"] is True and "session_key" not in calls[0]
    service.save_skill("default", {**skill, "capabilities": []})
    assert device_manage(inv)["error"] == "configuration_not_allowed_by_skill"
    assert len(calls) == 1
    for invalid in [True, "configuration_write", ["shell"], [None], [{}]]:
        with pytest.raises(ValueError, match="unsupported capability"):
            service.save_skill("default", {**skill, "capabilities": invalid})


def test_configuration_revalidates_authority_at_service_boundary(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    conn = _register_connection("default", {"name": "CE", "host": "127.0.0.1", "protocol": "telnet", "vendor": "h3c"})
    monkeypatch.setattr(service, "probe_target", lambda *a, **kw: pytest.fail("must not open a socket"))
    result = service.test_connection("default", conn["connection_id"], commands=["system-view"], configuration_skill_id="deleted")
    assert result["ok"] is False
    assert result["error"] == "configuration_not_allowed_by_skill"


def test_configuration_failure_retains_unknown_effects(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    conn = _register_connection("default", {"name": "CE", "host": "127.0.0.1", "protocol": "telnet", "vendor": "h3c"})
    skill = service.save_skill("default", {"name": "test", "device_ids": [conn["device_id"]], "connection_ids": [conn["connection_id"]], "capabilities": ["configuration_write"]})
    monkeypatch.setattr(service, "probe_target", lambda *a, **kw: {"ok": False, "status": "unknown", "execution_may_continue": True, "error": "configuration_outcome_unknown"})
    inv = SimpleNamespace(workspace_id="default", skill=skill["skill_id"], arguments={"action": "configure", "connection_id": conn["connection_id"], "commands": ["system-view"]})
    result = device_manage(inv)
    assert result["ok"] is False and result["status"] == "unknown"
    assert result["execution_may_continue"] and not result["automatic_retry_allowed"]


def test_configuration_contract_is_high_risk_and_cannot_retry():
    from extensions.runtime import get_extension_tool_specs
    from core.tools.policy import ToolPolicy
    from core.tools.schemas import ToolInvocation
    spec = next(s for s, _ in get_extension_tool_specs() if s.tool_id == "network.operations.device.manage")
    contract = spec.metadata["action_execution_contracts"]["configure"]
    assert contract["side_effects"] == "external_write"
    assert contract["idempotency"] == "unsafe_to_retry" and not contract["read_only"]
    decision = ToolPolicy().check(spec, ToolInvocation(tool_id=spec.tool_id, workspace_id="default", arguments={"action": "configure", "connection_id": "test", "commands": ["system-view"]}))
    assert decision.allowed and decision.risk_level == "high"


def _register_connection(workspace_id, payload):
    device = service.save_device(workspace_id, payload)
    return service.save_connection(workspace_id, {
        **payload, "device_id": device["device_id"],
        "passphrase": payload.get("key_passphrase", ""),
    }, auto_test=False)


def _execution_collector(collector):
    """Adapt a deterministic test transcript to the production evidence contract."""
    def execute(target, commands, **_kwargs):
        output = collector(target, commands)
        return {"ok": True, "read_ok": True, "output": output,
                "command_results": [{"command": c, "output": v, "complete": True, "error_code": ""}
                                    for c, v in output.items()]}
    return execute


def _run_inspection(monkeypatch, workspace_id, connection_ids=None, commands=None, script_id="", *, collector=None, background=False):
    """Exercise the production durable queue; never start a test-only worker."""
    assert not background
    from jobs.runner import run_job
    with monkeypatch.context() as scoped:
        if collector is not None:
            scoped.setattr(service, "collect_connection", _execution_collector(collector))
        task = service.enqueue_connection_inspection(workspace_id, connection_ids, commands, script_id)
        run_job(workspace_id, task["job_id"])
        return service.get_inspection(workspace_id, task["task_id"])


def test_connections_store_only_an_encrypted_credential_reference(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "核心交换机", "host": "10.0.0.1", "port": 22,
        "username": "netops", "password": "sensitive-password", "vendor": "h3c",
    })
    assert asset["credential_configured"] is True
    assert "sensitive-password" not in json.dumps(service.list_connections("default"))
    persisted = (tmp_path / "workspaces" / "_runtime" / "secrets" / "encrypted.json").read_text()
    assert "sensitive-password" not in persisted


def test_private_key_connections_do_not_expose_plaintext(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "汇聚交换机", "host": "10.0.0.9", "port": 22,
        "username": "netops", "auth_method": "private_key",
        "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-key\n-----END OPENSSH PRIVATE KEY-----",
        "key_passphrase": "secret-passphrase", "vendor": "huawei",
    })
    assert asset["credential_configured"] is True
    assert asset["auth_method"] == "private_key"
    visible = json.dumps(service.list_connections("default"), ensure_ascii=False)
    assert "secret-key" not in visible
    assert "secret-passphrase" not in visible
    persisted = (tmp_path / "workspaces" / "_runtime" / "secrets" / "encrypted.json").read_text()
    assert "secret-key" not in persisted
    assert "secret-passphrase" not in persisted


def test_telnet_connection_supports_optional_credentials_custom_port_and_skill_binding(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "Console-1", "host": "r1", "vendor": "h3c"})
    captured = {}
    def fake_probe(target, **_kwargs):
        captured.update({"protocol": target.protocol, "port": target.port, "username": target.credential.username, "source_address": target.source_address})
        return {"ok": True, "status": "succeeded", "duration_ms": 4, "stages": []}
    monkeypatch.setattr(service, "probe_target", fake_probe)
    connection = service.save_connection("default", {"device_id": device["device_id"], "protocol": "telnet", "port": 2323, "auth_method": "none", "source_address": "100.64.0.10"})
    assert captured == {"protocol": "telnet", "port": 2323, "username": "", "source_address": "100.64.0.10"}
    assert connection["verified"] is True
    skill = service.save_skill("default", {"name": "只读巡检", "device_ids": [device["device_id"]], "connection_ids": [connection["connection_id"]]})
    resolved = service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"], "device_ids": [device["device_id"]]})
    assert resolved["connection_ids"] == [connection["connection_id"]]
    result = device_manage(SimpleNamespace(workspace_id="default", skill=skill["skill_id"], arguments={"action": "probe", "connection_id": connection["connection_id"]}))
    assert result["ok"] is True


def test_connection_rejects_invalid_source_address(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "Console-1", "host": "r1", "vendor": "h3c"})

    with pytest.raises(ValueError, match="source_address must be a local IP address"):
        service.save_connection(
            "default",
            {
                "device_id": device["device_id"],
                "protocol": "telnet",
                "port": 2323,
                "auth_method": "none",
                "source_address": "not-an-ip",
            },
        )


def test_same_device_protocol_and_port_update_one_logical_connection(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "CE1", "host": "100.117.194.25", "vendor": "h3c"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "status": "succeeded", "duration_ms": 3})
    first = service.save_connection("default", {
        "device_id": device["device_id"], "name": "首次登记", "protocol": "telnet", "port": 30001, "auth_method": "none",
    })
    skill = service.save_skill("default", {
        "name": "测试 Skill", "device_ids": [device["device_id"]], "connection_ids": [first["connection_id"]],
    })

    updated = service.save_connection("default", {
        "device_id": device["device_id"], "name": "更新后的连接", "protocol": "telnet", "port": 30001, "auth_method": "none",
    })

    assert updated["connection_id"] == first["connection_id"]
    assert updated["name"] == "更新后的连接"
    assert len(service.list_connections("default")) == 1
    assert service.get_skill("default", skill["skill_id"])["connection_ids"] == [first["connection_id"]]


def test_device_identity_requires_both_name_and_host(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "status": "succeeded"})

    ce1 = service.save_device("default", {"name": "CE1", "host": "100.117.194.25", "vendor": "h3c"})
    ce2 = service.save_device("default", {"name": "CE2", "host": "100.117.194.25", "vendor": "h3c"})
    ce1_other_host = service.save_device("default", {"name": "CE1", "host": "100.117.194.26", "vendor": "h3c"})

    assert len({ce1["device_id"], ce2["device_id"], ce1_other_host["device_id"]}) == 3
    with pytest.raises(ValueError, match="device name and host already exist"):
        service.save_device("default", {"name": " ce1 ", "host": "100.117.194.25", "vendor": "h3c"})

    first = service.save_connection("default", {
        "device_id": ce1["device_id"], "protocol": "telnet", "port": 30001, "auth_method": "none",
    })
    second = service.save_connection("default", {
        "device_id": ce2["device_id"], "protocol": "telnet", "port": 30002, "auth_method": "none",
    })
    assert first["connection_id"] != second["connection_id"]
    assert {(item["device_id"], item["port"]) for item in service.list_connections("default")} == {
        (ce1["device_id"], 30001),
        (ce2["device_id"], 30002),
    }


def test_legacy_duplicate_connections_are_merged_without_dangling_skill_refs(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "CE1", "host": "100.117.194.25", "vendor": "h3c"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "status": "succeeded"})
    canonical = service.save_connection("default", {
        "device_id": device["device_id"], "protocol": "telnet", "port": 30001, "auth_method": "none",
    })
    skill = service.save_skill("default", {
        "name": "测试 Skill", "device_ids": [device["device_id"]], "connection_ids": [canonical["connection_id"]],
    })
    duplicate = {
        **service.get_connection("default", canonical["connection_id"], include_secret=True),
        "connection_id": "connection_legacy_duplicate",
        "updated_at": "2099-01-01T00:00:00+00:00",
    }
    service._store("default").save("connections", duplicate["connection_id"], duplicate)

    visible = service.list_connections("default")

    assert len(visible) == 2  # GET must not rewrite records or Skill bindings.
    assert service.get_connection("default", duplicate["connection_id"]) is not None
    assert service.reconcile_duplicate_connections("default") == 1
    assert service.reconcile_duplicate_connections("default") == 0
    visible = service.list_connections("default")

    assert [item["connection_id"] for item in visible] == [canonical["connection_id"]]
    assert service.get_connection("default", duplicate["connection_id"]) is None
    assert service.get_skill("default", skill["skill_id"])["connection_ids"] == [canonical["connection_id"]]


def test_source_address_is_automatically_selected_for_vpn_scope(monkeypatch):
    monkeypatch.setattr(
        "extensions.network_operations.device_tools.local_ipv4_addresses",
        lambda: ["192.168.5.12", "100.124.182.34", "198.19.0.1"],
    )
    assert resolve_source_address("100.117.194.25") == "100.124.182.34"
    assert resolve_source_address("8.8.8.8") == ""
    assert resolve_source_address("100.117.194.25", "100.64.1.2") == "100.64.1.2"


def test_skill_keeps_configured_connection_when_last_probe_failed(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": False, "status": "failed", "error": "offline"})
    connection = service.save_connection("default", {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"})
    skill = service.save_skill("default", {"name": "可主动重连", "device_ids": [device["device_id"]], "connection_ids": [connection["connection_id"]]})

    assert skill["connection_ids"] == [connection["connection_id"]]
    assert service.workbench_skill_catalog("default")[0]["resources"][0]["resource_id"] == device["device_id"]


def test_workbench_selection_is_pure_and_never_expands_explicit_empty_scope(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    devices = [service.save_device("default", {"name": f"R{i}", "host": f"10.0.0.{i}"}) for i in range(1, 7)]
    connections = [service.save_connection("default", {"device_id": d["device_id"], "protocol": "telnet", "auth_method": "none"}, auto_test=False) for d in devices]
    skill = service.save_skill("default", {"name": "six", "device_ids": [d["device_id"] for d in devices],
                                         "connection_ids": [c["connection_id"] for c in connections]})
    def forbidden_probe(*args, **kwargs):
        raise AssertionError("Skill selection must not open any connection")
    monkeypatch.setattr(service, "probe_target", forbidden_probe)
    before = service._raw_connections("default")
    for _ in range(3):
        resolved = service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"]})
        assert len(resolved["connection_ids"]) == 6
        assert resolved["connection_policy"] == "on_demand"
        assert all(c["current_reachability"] == "not_checked" for c in resolved["connections"])
        assert "ready_connection_ids" not in resolved
        assert "connection_activation" not in resolved
    assert service._raw_connections("default") == before
    subset = service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"], "resource_ids": [d["device_id"] for d in devices[:2]]})
    assert subset["connection_ids"] == [c["connection_id"] for c in connections[:2]]
    for field in ("resource_ids", "device_ids"):
        with pytest.raises(ValueError, match="workbench_skill_device_forbidden"):
            service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"], field: []})


def test_workbench_selection_keeps_failed_history_without_claiming_current_failure(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    monkeypatch.setattr(service, "probe_target", lambda *_a, **_k: {"ok": False, "error": "offline"})
    connection = service.save_connection("default", {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"})
    skill = service.save_skill("default", {"name": "offline", "device_ids": [device["device_id"]], "connection_ids": [connection["connection_id"]]})
    resolved = service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"]})
    assert resolved["connections"][0]["last_observed_status"] == "failed"
    assert resolved["connections"][0]["current_reachability"] == "not_checked"
    assert "degraded" not in resolved


def test_first_inspection_is_observation_and_candidate_not_normal_baseline(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    connection = _register_connection("default", {
        "name": "R1", "host": "10.0.0.1", "protocol": "telnet", "vendor": "h3c",
    })
    task = _run_inspection(
        monkeypatch, "default", [connection["connection_id"]], ["display version"],
        collector=lambda _target, commands: {commands[0]: "Comware Software"},
    )

    observation = service.list_observations("default")[0]
    candidate = service.list_references("default")[0]
    assert task["observation_id"] == observation["observation_id"]
    assert observation["authoritative_for_normal"] is False
    assert candidate["state"] == "candidate"
    assert candidate["authority"] == "observed"
    assert candidate["current"] is False
    assert service.list_baselines("default") == []

    confirmed = service.transition_reference("default", candidate["reference_id"], "confirm")
    assert confirmed["state"] == "confirmed"
    assert confirmed["authority"] == "user_confirmed"
    assert confirmed["current"] is True
    assert service.list_baselines("default")[0]["baseline_id"] == confirmed["reference_id"]

    later = service.record_inspection_observation("default", {
        "task_id": "inspection_later", "status": "succeeded", "finished_at": "2026-09-07T00:00:00Z",
        "artifact_id": "artifact-2", "results": {connection["connection_id"]: {"status": "succeeded", "output_hash": "changed"}},
    })
    assert service.list_baselines("default")[0]["baseline_id"] == confirmed["reference_id"]
    replacement = service.transition_reference("default", later["candidate_reference_id"], "confirm")
    old = next(item for item in service.list_references("default") if item["reference_id"] == confirmed["reference_id"])
    assert replacement["current"] is True
    assert old["state"] == "superseded" and old["current"] is False


def test_partial_observation_cannot_be_confirmed_as_expected_state(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    connection = _register_connection("default", {
        "name": "R1", "host": "10.0.0.1", "protocol": "telnet", "vendor": "h3c",
    })
    task = {
        "task_id": "inspection_partial", "status": "partial", "finished_at": "2026-09-06T00:00:00Z",
        "artifact_id": "artifact-1", "results": {connection["connection_id"]: {"status": "partial", "output_hash": "hash"}},
    }
    observation = service.record_inspection_observation("default", task)

    with pytest.raises(ValueError, match="complete_observation_required_for_confirmation"):
        service.transition_reference("default", observation["candidate_reference_id"], "confirm")


def test_command_experience_is_advisory_and_scoped(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    connection = _register_connection("default", {
        "name": "R1", "host": "10.0.0.1", "protocol": "telnet", "vendor": "h3c",
    })
    records = service.record_command_experience("default", connection["connection_id"], {
        "device_profile": {"driver_id": "h3c.comware"},
        "command_results": [{"command": "display version", "complete": True, "error_code": "", "truncated": False}],
    })
    context = service.operational_context("default", connection_ids=[connection["connection_id"]])

    assert records[0]["status"] == "accepted"
    assert records[0]["advisory_only"] is True
    assert context["command_experience"][0]["command"] == "display version"
    assert context["first_observation_rule"] == "never_assume_normal"

    empty_scope = service.operational_context("default", connection_ids=[])
    assert empty_scope["observations"] == []
    assert empty_scope["references"] == []
    assert empty_scope["command_experience"] == []


def test_device_manage_syntax_rejection_returns_model_guidance_without_runtime_call(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    connection = _register_connection("default", {
        "name": "R1", "host": "10.0.0.1", "protocol": "telnet", "vendor": "h3c",
    })
    skill = service.save_skill("default", {
        "name": "read", "device_ids": [connection["device_id"]],
        "connection_ids": [connection["connection_id"]],
    })
    monkeypatch.setattr(service, "test_connection", lambda *_args, **_kwargs: {
        "ok": True, "read_ok": False,
        "device_profile": {"driver_id": "h3c.comware", "vendor": "h3c", "semantic_facts": ["bgp_peers"]},
        "command_results": [{
            "command": "display bgp peer vpn4", "complete": True,
            "error_code": "device_command_rejected", "device_error": "% Unrecognized command",
        }],
    })
    result = device_manage(SimpleNamespace(
        workspace_id="default", skill=skill["skill_id"],
        skill_connection_ids=(connection["connection_id"],),
        arguments={"action": "read", "connection_id": connection["connection_id"], "commands": ["display bgp peer vpn4"]},
    ))

    assert result["model_recovery_guidance"][0]["decision_owner"] == "llm"
    assert result["command_experience"][0]["status"] == "rejected"
    assert "runtime_recoveries" not in result
    assert "runtime_recovery" not in result


def test_device_manage_reconnects_expired_authorized_connection(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": False, "status": "failed", "error": "offline"})
    connection = service.save_connection("default", {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"})
    skill = service.save_skill("default", {"name": "主动连接", "device_ids": [device["device_id"]], "connection_ids": [connection["connection_id"]]})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "status": "succeeded", "duration_ms": 4})

    result = device_manage(SimpleNamespace(
        workspace_id="default", skill=skill["skill_id"],
        arguments={"action": "probe", "connection_id": connection["connection_id"]},
    ))

    assert result["ok"] is True
    assert result["connection_ok"] is True
    assert result["connection"]["verified"] is True


def test_device_manage_returns_unavailable_connection_as_llm_decision_evidence(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    connection = service.save_connection("default", {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"})
    skill = service.save_skill("default", {"name": "主动连接", "device_ids": [device["device_id"]], "connection_ids": [connection["connection_id"]]})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": False, "status": "failed", "error": "timed out"})

    result = device_manage(SimpleNamespace(
        workspace_id="default", skill=skill["skill_id"],
        arguments={"action": "probe", "connection_id": connection["connection_id"]},
    ))

    assert result["ok"] is True
    assert result["connection_ok"] is False
    assert result["decision_required"] is True
    assert result["error"] == "timed out"
    assert result["device_id"] == device["device_id"]


def test_workbench_selected_connection_boundary_is_enforced(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    first = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    second = service.save_device("default", {"name": "R2", "host": "10.0.0.2"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    first_connection = service.save_connection("default", {"device_id": first["device_id"], "protocol": "telnet", "auth_method": "none"})
    second_connection = service.save_connection("default", {"device_id": second["device_id"], "protocol": "telnet", "auth_method": "none"})
    skill = service.save_skill("default", {
        "name": "双设备",
        "device_ids": [first["device_id"], second["device_id"]],
        "connection_ids": [first_connection["connection_id"], second_connection["connection_id"]],
    })
    invocation = SimpleNamespace(
        workspace_id="default", skill=skill["skill_id"],
        skill_connection_ids=(first_connection["connection_id"],),
        arguments={"action": "probe", "connection_id": second_connection["connection_id"]},
    )

    assert device_manage(invocation) == {"ok": False, "error": "connection_not_selected_in_workbench"}


def test_workbench_skill_scope_filters_inventory_skills_and_inspection_lifecycle(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    first = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    second = service.save_device("default", {"name": "R2", "host": "10.0.0.2"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    first_connection = service.save_connection("default", {
        "device_id": first["device_id"], "protocol": "telnet", "auth_method": "none",
    })
    second_connection = service.save_connection("default", {
        "device_id": second["device_id"], "protocol": "telnet", "auth_method": "none",
    })
    selected_skill = service.save_skill("default", {
        "name": "Selected", "device_ids": [first["device_id"], second["device_id"]],
        "connection_ids": [first_connection["connection_id"], second_connection["connection_id"]],
    })
    foreign_skill = service.save_skill("default", {
        "name": "Foreign", "device_ids": [second["device_id"]],
        "connection_ids": [second_connection["connection_id"]],
    })
    invocation = SimpleNamespace(
        workspace_id="default", skill=selected_skill["skill_id"],
        skill_connection_ids=(first_connection["connection_id"],), arguments={},
    )

    inventory = devices_read(invocation)
    assert [item["device_id"] for item in inventory["devices"]] == [first["device_id"]]
    assert inventory["connection_ids"] == [first_connection["connection_id"]]
    invocation.arguments = {"device_id": second["device_id"]}
    assert devices_read(invocation)["error"] == "device_not_allowed_by_skill"
    invocation.arguments = {"skill_id": foreign_skill["skill_id"]}
    assert skills_read(invocation)["error"] == "skill_not_selected_in_workbench"

    foreign_task = {
        "task_id": "inspection_foreign", "status": "failed",
        "connection_ids": [second_connection["connection_id"]],
        "total": 1, "completed": 1, "failed": 1, "results": {},
    }
    monkeypatch.setattr(service, "get_inspection", lambda *_args: foreign_task)
    monkeypatch.setattr(service, "list_inspections", lambda *_args: [foreign_task])
    retry_calls = []
    cancel_calls = []
    monkeypatch.setattr(service, "retry_inspection", lambda *_args: retry_calls.append(_args))
    monkeypatch.setattr(service, "cancel_inspection", lambda *_args: cancel_calls.append(_args))
    for action in ("get", "retry", "cancel"):
        invocation.arguments = {"action": action, "task_id": foreign_task["task_id"]}
        assert inspection(invocation)["error"] == "inspection_not_allowed_by_skill"
    invocation.arguments = {"action": "list"}
    assert inspection(invocation)["inspections"] == []
    assert retry_calls == [] and cancel_calls == []


def test_probe_requires_and_then_saves_host_key(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "核心交换机", "host": "10.0.0.1", "username": "netops",
        "password": "sensitive-password", "vendor": "h3c",
    })

    def fake_probe(_target, *, accept_host_key=False, read=False, **_kwargs):
        if not accept_host_key:
            return {"ok": False, "status": "blocked", "fingerprint": "SHA256:test", "requires_host_key_acceptance": True}
        return {"ok": True, "status": "succeeded", "fingerprint": "SHA256:test", "stages": [{"name": "auth", "status": "ok"}]}

    monkeypatch.setattr(service, "probe_target", fake_probe)
    blocked = service.test_connection("default", asset["connection_id"])
    assert blocked["requires_host_key_acceptance"] is True
    accepted = service.test_connection("default", asset["connection_id"], accept_host_key=True)
    assert accepted["ok"] is True
    assert accepted["connection"]["host_key_fingerprint"] == "SHA256:test"
    assert service.get_connection("default", asset["connection_id"])["host_key_fingerprint"] == "SHA256:test"


def test_health_findings_are_evidence_backed_and_keep_human_state(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "核心交换机", "host": "10.0.0.1", "username": "netops",
        "password": "sensitive-password", "vendor": "h3c",
    })
    script = service.save_inspection_script("default", {
        "name": "告警验证", "description": "验证规则化发现项", "vendors": ["h3c"],
        "commands": ["display logbuffer"],
        "checks": [{
            "check_id": "alarm", "name": "发现告警", "description": "命中告警文本。",
            "severity": "high", "kind": "output_matches", "pattern": "ALARM",
        }],
    })
    task = _run_inspection(monkeypatch,
        "default", [asset["connection_id"]], script_id=script["script_id"],
        collector=lambda _asset, commands: {command: "ALARM: link down" for command in commands},
        background=False,
    )
    assert task["status"] == "succeeded"
    findings = service.list_findings("default")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["title"] == "发现告警"
    assert finding["last_seen_task_id"] == task["task_id"]
    assert "ALARM" not in json.dumps(finding)

    # Retain human state from historical records without a retired write API.
    finding["status"] = "acknowledged"
    service._store("default").save("findings", finding["finding_id"], finding)
    repeat = _run_inspection(monkeypatch,
        "default", [asset["connection_id"]], script_id=script["script_id"],
        collector=lambda _asset, commands: {command: "ALARM: link down" for command in commands},
        background=False,
    )
    assert repeat["status"] == "succeeded"
    persisted = service.list_findings("default")[0]
    assert persisted["status"] == "acknowledged"
    assert persisted["occurrences"] == 2
    assert len(service.list_findings("default")) == 1

    filtered = service.list_findings("default", severity="high")
    assert filtered[0]["finding_id"] == finding["finding_id"]


def test_write_commands_are_rejected():
    assert device_is_read_only_command("display version") is True
    assert device_is_read_only_command("show interfaces status") is True
    assert device_is_read_only_command("system-view") is False
    assert device_is_read_only_command("reload") is False
    assert device_is_read_only_command("display version; reboot") is False
    assert device_is_read_only_command("display version && reboot", "h3c") is False
    assert device_is_read_only_command("display $(reboot)", "h3c") is False
    assert device_is_read_only_command("rm -rf /", "generic") is False
    assert device_is_read_only_command("show version", "h3c") is False
    assert device_is_read_only_command("display version", "cisco") is False
    assert device_is_read_only_command("ip address", "generic") is True
    with pytest.raises(ValueError, match="commands_must_be_read_only"):
        service.commands_for({"vendor": "h3c"}, ["reboot"])


def test_read_only_command_boundary_requires_an_array_and_matches_vendor():
    for invalid in ("display version", ["display version", 1], None):
        try:
            normalize_read_only_commands(invalid)  # type: ignore[arg-type]
        except ValueError as exc:
            assert str(exc) == "commands must be an array of strings"
        else:
            raise AssertionError("malformed command collections must fail closed")
    for starter in service.STARTER_SCRIPTS:
        assert normalize_read_only_commands(starter["commands"], starter["vendors"][0]) == starter["commands"]


def test_read_only_command_limit_is_shared_and_enforced():
    commands = ["display version"] * (MAX_READ_ONLY_COMMANDS + 1)
    try:
        service.commands_for({"vendor": "h3c"}, commands)
    except ValueError as exc:
        assert "1 to 20" in str(exc)
    else:
        raise AssertionError("command count above the shared limit must fail")


def test_device_and_skill_catalog_use_current_entities(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    first = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    second = service.save_device("default", {"name": "R2", "host": "10.0.0.2"})
    result = devices_read(SimpleNamespace(workspace_id="default", arguments={}))
    assert {item["device_id"] for item in result["devices"]} == {first["device_id"], second["device_id"]}
    monkeypatch.setattr(service, "get_connection", lambda *_args, **_kwargs: {"connection_id": "connection_1", "device_id": first["device_id"], "verified": True})
    skill = service.save_skill("default", {"name": "核心设备", "device_ids": [first["device_id"]], "connection_ids": ["connection_1"]})
    listed = skills_read(SimpleNamespace(workspace_id="default", arguments={}, skill=None))
    assert listed["skills"][0]["skill_id"] == skill["skill_id"]


def test_network_reads_report_missing_records_as_failures(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    missing_asset = devices_read(SimpleNamespace(
        workspace_id="default", arguments={"device_id": "device_missing"},
    ))
    missing_task = inspection(SimpleNamespace(
        workspace_id="default", arguments={"action": "get", "task_id": "inspection_missing"},
    ))
    assert missing_asset == {"ok": False, "error": "device_not_found", "device_id": "device_missing"}
    assert missing_task == {"ok": False, "error": "inspection_not_found", "task_id": "inspection_missing"}


def test_device_manage_is_registered_as_network_operations_extension_tool():
    from extensions.runtime import get_extension_tool_specs

    specs = {spec.tool_id: spec for spec, _handler in get_extension_tool_specs()}
    assert "network.operations.device.manage" in specs
    spec = specs["network.operations.device.manage"]
    assert spec.category == "ops"
    assert spec.risk_level == "medium"
    assert spec.permission_action == "network"
    properties = set((spec.input_schema or {}).get("properties") or {})
    for required in {"action", "connection_id", "commands"}:
        assert required in properties, required


def test_extension_tools_are_registered_with_runtime_risk_contracts():
    from core.runtime_engine.contracts import get_contract

    contract = get_contract("network.operations.device.manage")
    assert contract is not None
    assert contract.risk_level == "medium"
    assert contract.side_effect == "external_request"


def test_read_only_extension_tools_get_safe_retry_contracts():
    from core.runtime_engine.contracts import get_retry_contract

    contract = get_retry_contract("network.operations.devices_read", {})
    assert contract is not None
    assert contract.idempotent is True
    assert contract.max_retries >= 1


def test_device_manage_is_exposed_to_llm_as_extension_tool():
    from core.tools.canonical_registry import CANONICAL_REGISTRY, to_openai_tools, to_tool_specs

    assert "device.manage" not in CANONICAL_REGISTRY
    tool_specs = {spec.tool_id: spec for spec, _handler in to_tool_specs()}
    assert "network.operations.device.manage" in tool_specs
    openai_names = {
        tool["function"]["name"]
        for tool in to_openai_tools()
        if tool.get("type") == "function"
    }
    assert "device__manage" not in openai_names
    assert "network__operations__device__manage" in openai_names


def test_network_extension_llm_descriptions_expose_actions_and_arguments():
    from agent.runtime.ssot_runtime import _build_ssot_runtime_tool_registry
    from core.runtime_engine.query_loop import _build_cached_tool_definitions

    registry = _build_ssot_runtime_tool_registry([
        "network.operations.device.manage",
        "network.operations.inspection",
    ])
    descriptions = {
        tool["function"]["name"]: tool["function"]["description"]
        for tool in _build_cached_tool_definitions(registry)
    }

    device = descriptions["network__operations__device__manage"]
    assert "probe=network" in device
    assert "read=network" in device
    assert "connection_id" in device
    assert "commands" in device

    inspection = descriptions["network__operations__inspection"]
    assert "run=network" in inspection
    assert "get=network" in inspection
    assert "cancel=network" in inspection
    assert "connection_ids" in inspection
    assert "task_id" in inspection


def test_workbench_catalog_exposes_configured_resources_regardless_of_last_probe(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    connection = service.save_connection("default", {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"})
    skill = service.save_skill("default", {"name": "核心巡检", "device_ids": [device["device_id"]], "connection_ids": [connection["connection_id"]]})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": False, "error": "expired"})
    service.test_connection("default", connection["connection_id"])
    catalog = service.workbench_skill_catalog("default")
    assert catalog == [{
        "skill_id": skill["skill_id"], "name": "核心巡检", "description": "",
        "resources": [{"resource_id": device["device_id"], "name": "R1", "description": "10.0.0.1", "kind": "network_device"}],
        "default_resource_ids": [device["device_id"]], "selection_mode": "multiple",
    }]


def test_connection_inspection_keeps_connection_identity_end_to_end(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1", "vendor": "h3c"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    connection = service.save_connection(
        "default",
        {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"},
    )
    task, targets, script = service._new_connection_inspection_task(
        "default", [connection["connection_id"]], ["display version"], "",
    )
    serialized = json.dumps(task, ensure_ascii=False)
    assert '"asset_id"' not in serialized
    assert '"asset_ids"' not in serialized
    assert task["target_kind"] == "connection"
    assert task["connection_ids"] == [connection["connection_id"]]
    assert task["device_ids"] == [device["device_id"]]

    service._store("default").save("inspections", task["task_id"], task)
    service._execute_inspection(
        "default", task["task_id"], targets, ["display version"],
        lambda _target, commands: {command: "ok" for command in commands},
        threading.Event(), script,
    )
    evidence = service.inspection_evidence_summary("default", task["task_id"])
    assert evidence["devices"][0]["connection_id"] == connection["connection_id"]
    assert evidence["devices"][0]["device_id"] == device["device_id"]
    assert "asset_id" not in evidence["devices"][0]


def test_connection_inspection_isolates_expired_target_failure(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    first = service.save_device("default", {"name": "R1", "host": "10.0.0.1", "vendor": "h3c"})
    second = service.save_device("default", {"name": "R2", "host": "10.0.0.2", "vendor": "h3c"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    first_connection = service.save_connection("default", {"device_id": first["device_id"], "protocol": "telnet", "auth_method": "none"})
    second_connection = service.save_connection("default", {"device_id": second["device_id"], "protocol": "telnet", "auth_method": "none"})
    second_record = service.get_connection("default", second_connection["connection_id"], include_secret=True)
    second_record["status"] = "failed"
    service._store("default").save("connections", second_connection["connection_id"], second_record)

    task, targets, script = service._new_connection_inspection_task(
        "default", [first_connection["connection_id"], second_connection["connection_id"]], ["display version"], "",
    )
    service._store("default").save("inspections", task["task_id"], task)

    def collector(target, commands):
        if target["device_id"] == second["device_id"]:
            raise RuntimeError("connection timed out")
        return {command: "ok" for command in commands}

    service._execute_inspection(
        "default", task["task_id"], targets, ["display version"],
        _execution_collector(collector), threading.Event(), script,
    )
    completed = service.get_inspection("default", task["task_id"])

    assert completed["status"] == "partial"
    assert completed["succeeded"] == 1
    assert completed["failed"] == 1
    assert completed["results"][second_connection["connection_id"]]["error"] == "connection timed out"


def test_hard_deleting_last_connection_removes_depleted_skill(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    connection = service.save_connection("default", {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"})
    skill = service.save_skill("default", {"name": "临时巡检", "device_ids": [device["device_id"]], "connection_ids": [connection["connection_id"]]})
    assert service.delete_connection("default", connection["connection_id"]) is True
    assert service.get_skill("default", skill["skill_id"]) is None


def test_skill_tool_allowlist_is_validated_and_enforced(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    connection = service.save_connection("default", {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"})
    skill = service.save_skill("default", {
        "name": "仅巡检", "device_ids": [device["device_id"]],
        "connection_ids": [connection["connection_id"]],
        "allowed_tool_ids": ["network.operations.inspection"],
    })
    inv = SimpleNamespace(workspace_id="default", skill=skill["skill_id"], arguments={"action": "probe", "connection_id": connection["connection_id"]})
    assert device_manage(inv)["ok"]
    blocked = devices_read(inv)
    assert blocked == {"ok": False, "error": "tool_not_allowed_by_skill"}


def test_legacy_skill_read_capability_is_intrinsic_without_granting_writes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    conn = _register_connection("default", {"name": "CE", "host": "127.0.0.1", "protocol": "telnet", "vendor": "h3c"})
    skill = service.save_skill("default", {"name": "test", "device_ids": [conn["device_id"]], "connection_ids": [conn["connection_id"]], "allowed_tool_ids": []})
    assert skill["allowed_tool_ids"] == [service.SKILL_BASE_TOOL_ID, "network.operations.context_read"]
    service._store("default").save("skills", skill["skill_id"], {**skill, "allowed_tool_ids": []})
    for resolved in [service.get_skill("default", skill["skill_id"]), service.list_skills("default")[0], service.resolve_workbench_selection("default", {"skill_id": skill["skill_id"]})]:
        assert resolved["allowed_tool_ids"] == [service.SKILL_BASE_TOOL_ID, "network.operations.context_read"]
        assert resolved["capabilities"] == []
    inv = SimpleNamespace(workspace_id="default", skill=skill["skill_id"], arguments={"action": "configure", "connection_id": conn["connection_id"], "commands": ["system-view"]})
    assert device_manage(inv)["error"] == "configuration_not_allowed_by_skill"
    service.save_skill("default", {**skill, "enabled": False})
    inv.arguments["action"] = "probe"
    assert device_manage(inv)["error"] == "tool_not_allowed_by_skill"


def test_switching_connection_auth_removes_obsolete_secret_refs(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    connection = service.save_connection("default", {
        "device_id": device["device_id"], "protocol": "telnet", "auth_method": "password",
        "username": "ops", "password": "secret-value",
    })
    stored = service.get_connection("default", connection["connection_id"], include_secret=True)
    assert stored and stored["password_ref"]
    service.save_connection("default", {
        "connection_id": connection["connection_id"], "device_id": device["device_id"],
        "protocol": "telnet", "auth_method": "none",
    })
    updated = service.get_connection("default", connection["connection_id"], include_secret=True)
    assert updated and updated["password_ref"] == ""


def test_device_identity_change_invalidates_connections_and_connection_cannot_move(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    first = service.save_device("default", {"name": "R1", "host": "10.0.0.1", "vendor": "h3c"})
    second = service.save_device("default", {"name": "R2", "host": "10.0.0.2", "vendor": "h3c"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True, "duration_ms": 1})
    connection = service.save_connection(
        "default", {"device_id": first["device_id"], "protocol": "telnet", "auth_method": "none"},
    )
    assert connection["verified"] is True

    service.save_device("default", {**first, "host": "10.0.0.9"})
    invalidated = service.get_connection("default", connection["connection_id"])
    assert invalidated and invalidated["verified"] is False
    assert invalidated["last_error"] == "device_identity_changed_retest_required"

    try:
        service.save_connection("default", {
            "connection_id": connection["connection_id"], "device_id": second["device_id"],
            "protocol": "telnet", "auth_method": "none",
        })
    except ValueError as exc:
        assert str(exc) == "connection_device_is_immutable"
    else:
        raise AssertionError("a connection identity must remain bound to its device")


def test_extension_routes_cover_device_connection_and_skill_flow(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("LZCORE_LOGIN_ENABLED", "false")
    from extensions.runtime import reset_extension_cache_for_tests
    reset_extension_cache_for_tests()
    from backend.main import create_app
    client = create_app().test_client()
    created = client.post("/api/extensions/network.operations/devices", json={
        "workspace_id": "default", "name": "R1", "host": "10.0.0.2", "vendor": "huawei",
    })
    assert created.status_code == 201
    listed = client.get("/api/extensions/network.operations/devices?workspace_id=default")
    assert listed.status_code == 200
    assert listed.get_json()["devices"][0]["name"] == "R1"
    skills = client.get("/api/extensions/network.operations/skills?workspace_id=default")
    assert skills.status_code == 200
    assert skills.get_json()["skills"] == []
    assert client.get("/api/extensions/network.operations/devices/device_missing").status_code == 400


def test_inspection_scripts_are_validated_and_snapshotted(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    starters = service.list_inspection_scripts("default")
    starter = next(item for item in starters if item["script_id"] == "starter-h3c-health")
    assert starter["builtin"] is False
    edited = service.save_inspection_script("default", {"script_id": starter["script_id"], "name": "核心 H3C 健康检查", "description": "用户调整后的默认脚本", "vendors": ["h3c"], "commands": ["display version", "display cpu-usage"]})
    assert edited["name"] == "核心 H3C 健康检查"
    assert edited["version"] == 2
    try:
        service.save_inspection_script("default", {"name": "危险脚本", "vendors": ["h3c"], "commands": ["reboot"]})
    except ValueError as exc:
        assert str(exc) == "commands_must_be_read_only"
    else:
        raise AssertionError("write command must be rejected")
    for invalid_vendors in ("h3c", [{"name": "h3c"}], ["all"]):
        try:
            service.save_inspection_script("default", {"name": "无效厂商", "vendors": invalid_vendors, "commands": ["display version"]})
        except ValueError as exc:
            assert "vendors" in str(exc)
        else:
            raise AssertionError("invalid vendor declarations must fail closed")
    script = service.save_inspection_script("default", {"name": "核心检查", "description": "读取版本和接口", "vendors": ["h3c"], "commands": ["display version", "display interface brief"]})
    assert script["readonly"] is True
    asset = _register_connection("default", {"name": "Core-1", "host": "10.0.0.1", "username": "ops", "password": "secret", "vendor": "h3c"})
    task = _run_inspection(monkeypatch, "default", [asset["connection_id"]], script_id=script["script_id"], collector=lambda _asset, commands: {command: "ok" for command in commands}, background=False)
    assert task["status"] == "succeeded"
    assert task["script"]["script_id"] == script["script_id"]
    assert task["results"][asset["connection_id"]]["commands"] == ["display version", "display interface brief"]
    huawei = _register_connection("default", {"name": "Agg-1", "host": "10.0.0.2", "username": "ops", "password": "secret", "vendor": "huawei"})
    try:
        _run_inspection(monkeypatch, "default", [huawei["connection_id"]], script_id=script["script_id"], background=False)
    except ValueError as exc:
        assert str(exc) == "script_not_supported_for_vendor:huawei"
    else:
        raise AssertionError("vendor mismatch must be rejected")

def test_inspection_script_http_routes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("LZCORE_LOGIN_ENABLED", "false")
    from extensions.runtime import reset_extension_cache_for_tests
    reset_extension_cache_for_tests()
    from backend.main import create_app
    client = create_app().test_client()
    listed = client.get("/api/extensions/network.operations/scripts?workspace_id=default")
    assert listed.status_code == 200
    scripts = listed.get_json()["scripts"]
    assert len(scripts) == 3
    assert all(item["builtin"] is False for item in scripts)
    assert any(item["script_id"] == "starter-huawei-health" for item in scripts)
    created = client.post("/api/extensions/network.operations/scripts", json={"workspace_id": "default", "name": "接口核查", "vendors": ["h3c"], "commands": ["display interface brief"]})
    assert created.status_code == 201
    assert created.get_json()["script"]["name"] == "接口核查"


def test_connection_inspection_evidence_summary(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    script = service.save_inspection_script("default", {
        "name": "结果闭环检查", "vendors": ["h3c"], "commands": ["display version"],
    })
    asset = _register_connection("default", {
        "name": "Core-1", "host": "10.0.0.9", "username": "ops", "password": "secret", "vendor": "h3c",
    })
    first = _run_inspection(monkeypatch,
        "default", [asset["connection_id"]], script_id=script["script_id"],
        collector=lambda _asset, commands: {command: "first" for command in commands}, background=False,
    )
    evidence = service.inspection_evidence_summary("default", first["task_id"])
    assert evidence["artifact_sensitivity"] == "secret"
    assert evidence["devices"][0]["connection_id"] == asset["connection_id"]
    assert evidence["devices"][0]["output_hash"]
    assert "raw_output" not in evidence["devices"][0]


def test_user_inspection_uses_durable_job_worker_and_cancel(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "collect_connection", _execution_collector(lambda _asset, commands: {command: "ok" for command in commands}))
    script = service.save_inspection_script("default", {
        "name": "持久 Worker 巡检", "vendors": ["h3c"], "commands": ["display version"],
    })
    asset = _register_connection("default", {
        "name": "Core-Worker", "host": "10.0.0.10", "username": "ops", "password": "secret", "vendor": "h3c",
    })
    task = service.enqueue_connection_inspection("default", [asset["connection_id"]], script_id=script["script_id"])
    assert task["status"] == "queued"
    assert task["job_id"]
    from jobs.runner import run_job
    from jobs.store import get_job
    run_job("default", task["job_id"])
    finished = service.get_inspection("default", task["task_id"])
    assert finished["status"] == "succeeded"
    assert get_job("default", task["job_id"]).status == "succeeded"
    queued = service.enqueue_connection_inspection("default", [asset["connection_id"]], script_id=script["script_id"])
    assert service.cancel_inspection("default", queued["task_id"]) is True
    assert service.get_inspection("default", queued["task_id"])["status"] == "cancelled"
    assert get_job("default", queued["job_id"]).status == "cancelled"


def test_inspection_selection_fails_closed(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "Core-Selection", "host": "10.0.0.20", "username": "ops", "password": "secret", "vendor": "h3c",
    })
    invalid = (None, [], [""], [asset["connection_id"], asset["connection_id"]], [asset["connection_id"], "asset_missing"])
    for asset_ids in invalid:
        try:
            service.enqueue_connection_inspection("default", asset_ids)  # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid asset selection must fail closed: {asset_ids!r}")


def test_durable_plans_require_explicit_commands_and_are_replayable(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "Core-Plan", "host": "10.0.0.21", "username": "ops", "password": "secret", "vendor": "h3c",
    })
    monkeypatch.setattr(service, "collect_connection", _execution_collector(lambda _asset, commands: {command: "ok" for command in commands}))
    from jobs.runner import run_job

    import pytest
    with pytest.raises(ValueError, match="exactly_one"):
        service.enqueue_connection_inspection("default", [asset["connection_id"]])
    with pytest.raises(ValueError, match="inspection_command_plan_invalid"):
        service._restore_command_plan({"command_plan": {"mode": "vendor_defaults"}})

    inline_task = service.enqueue_connection_inspection("default", [asset["connection_id"]], commands=["display version"])
    assert inline_task["command_plan"] == {"mode": "inline_commands", "commands": ["display version"]}
    run_job("default", inline_task["job_id"])
    finished_inline = service.get_inspection("default", inline_task["task_id"])
    assert finished_inline["results"][asset["connection_id"]]["commands"] == ["display version"]


def test_all_device_failures_fail_both_inspection_and_job(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "Core-Fail", "host": "10.0.0.22", "username": "ops", "password": "secret", "vendor": "h3c",
    })
    monkeypatch.setattr(service, "collect_connection", lambda _asset, _commands, **_kwargs: (_ for _ in ()).throw(RuntimeError("auth failed")))
    task = service.enqueue_connection_inspection("default", [asset["connection_id"]], commands=["display version"])
    from jobs.runner import run_job
    from jobs.store import get_job
    run_job("default", task["job_id"])
    assert service.get_inspection("default", task["task_id"])["status"] == "failed"
    assert get_job("default", task["job_id"]).status == "failed"


def test_inline_inspection_retry_preserves_immutable_command_plan(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "Core-Retry", "host": "10.0.0.23", "username": "ops", "password": "secret", "vendor": "h3c",
    })
    failed = _run_inspection(monkeypatch,
        "default", [asset["connection_id"]], commands=["display version"],
        collector=lambda _asset, _commands: (_ for _ in ()).throw(RuntimeError("temporary failure")),
        background=False,
    )
    assert failed["status"] == "failed"
    retried = service.retry_inspection("default", failed["task_id"])
    assert retried["retry_of_task_id"] == failed["task_id"]
    assert retried["command_plan"] == {"mode": "inline_commands", "commands": ["display version"]}


def test_script_inspection_retry_uses_original_snapshot_after_script_edit(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "Core-Snapshot", "host": "10.0.0.25", "username": "ops", "password": "secret", "vendor": "h3c",
    })
    script = service.save_inspection_script("default", {
        "name": "快照脚本", "vendors": ["h3c"], "commands": ["display version"],
    })
    failed = _run_inspection(monkeypatch,
        "default", [asset["connection_id"]], script_id=script["script_id"],
        collector=lambda _asset, _commands: (_ for _ in ()).throw(RuntimeError("temporary failure")),
        background=False,
    )
    service.save_inspection_script("default", {
        "script_id": script["script_id"], "name": "已修改脚本", "vendors": ["h3c"], "commands": ["display device"],
    })
    retried = service.retry_inspection("default", failed["task_id"])
    assert retried["command_plan"]["script"]["commands"] == ["display version"]
    assert retried["script"]["name"] == "快照脚本"


def test_llm_inspection_run_uses_durable_queue_and_exposes_retry(monkeypatch):
    captured = {}
    def fake_enqueue(workspace_id, connection_ids, commands, script_id="", *, created_by="user"):
        captured.update({"workspace_id": workspace_id, "connection_ids": connection_ids, "commands": commands, "script_id": script_id, "created_by": created_by})
        return {"task_id": "inspection_durable", "status": "queued"}
    monkeypatch.setattr(service, "enqueue_connection_inspection", fake_enqueue)
    result = inspection(SimpleNamespace(workspace_id="default", arguments={"action": "run", "connection_ids": ["connection_1"]}))
    assert result["task"]["status"] == "queued"
    assert result["coverage_status"] == "pending"
    assert result["tracking"]["task_id"] == "inspection_durable"
    assert result["tracking"]["done"] is False
    assert result["tracking"]["poll_arguments"] == {
        "action": "get", "task_id": "inspection_durable",
    }
    assert captured["created_by"] == "llm"

    monkeypatch.setattr(service, "retry_inspection", lambda workspace_id, task_id: {"task_id": "inspection_retry", "retry_of_task_id": task_id})
    retried = inspection(SimpleNamespace(workspace_id="default", arguments={"action": "retry", "task_id": "inspection_failed"}))
    assert retried["task"]["retry_of_task_id"] == "inspection_failed"


def test_llm_inspection_terminal_result_declares_coverage(monkeypatch):
    monkeypatch.setattr(service, "get_inspection", lambda *_args: {
        "task_id": "inspection_done", "status": "partial", "total": 6,
        "completed": 6, "succeeded": 5, "partial": 0, "failed": 1,
    })
    result = inspection(SimpleNamespace(
        workspace_id="default", arguments={"action": "get", "task_id": "inspection_done"},
    ))
    assert result["coverage_status"] == "partial"
    assert result["tracking"]["done"] is True
    assert result["tracking"]["suggested_next_action"] == "synthesize_results"


def test_inspection_analysis_projection_preserves_every_device(monkeypatch):
    monkeypatch.setattr(service, "get_inspection", lambda *_args: {
        "task_id": "inspection-two", "status": "succeeded", "total": 2,
        "completed": 2, "succeeded": 2, "partial": 0, "failed": 0,
        "results": {
            "connection-b": {
                "name": "PE-2", "status": "succeeded",
                "facts": {"current_config": {
                    "status": "collected", "characters": 20,
                    "content_hash": "b", "signals": {"identity": ["sysname PE-2"]},
                }},
                "command_results": [],
            },
            "connection-a": {
                "name": "PE-1", "status": "succeeded",
                "facts": {
                    "current_config": {
                        "status": "collected", "characters": 20,
                        "content_hash": "a", "signals": {"identity": ["sysname PE-1"]},
                    },
                    "bgp_peers": {
                        "status": "collected", "observation_status": "observed",
                        "observations": [{
                            "command": "display bgp peer ipv4",
                            "literal_excerpt": "1.1.1.1 65000 Established",
                        }],
                        "sources": [{"output_hash": "secret-detail"}],
                    },
                },
                "command_results": [],
            },
        },
    })

    result = inspection(SimpleNamespace(
        workspace_id="default", arguments={"action": "get", "task_id": "inspection-two"},
    ))
    projection = result["analysis_projection"]

    assert projection["coverage"] == {
        "total": 2, "completed": 2, "succeeded": 2, "partial": 0, "failed": 0,
    }
    assert [item["name"] for item in projection["devices"]] == ["PE-1", "PE-2"]
    assert projection["devices"][0]["fact_evidence"]["bgp_peers"]["observations"][0][
        "literal_excerpt"
    ] == "1.1.1.1 65000 Established"
    assert "sources" not in projection["devices"][0]["fact_evidence"]["bgp_peers"]
    assert projection["evidence_contract"]["collected_does_not_mean"].startswith("protocol_healthy")


def test_evidence_failure_transitions_task_to_terminal_failure(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    asset = _register_connection("default", {
        "name": "Core-Evidence", "host": "10.0.0.24", "username": "ops", "password": "secret", "vendor": "h3c",
    })
    monkeypatch.setattr(service, "_save_evidence_artifact", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk full")))
    failed = _run_inspection(monkeypatch,
        "default", [asset["connection_id"]], commands=["display version"], collector=lambda _asset, commands: {command: "ok" for command in commands}, background=False,
    )
    from jobs.store import get_job
    assert get_job("default", failed["job_id"]).status == "failed"
    assert failed["status"] == "failed"
    assert failed["error"] == "inspection_evidence_persist_failed"
