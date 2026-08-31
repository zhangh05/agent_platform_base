from __future__ import annotations

from collections import deque
from threading import Event
from types import SimpleNamespace
import pytest

from extensions.network_operations import service
from extensions.network_operations.backend import device_manage
from extensions.network_operations.cli_runtime import (
    InteractiveCLISession,
    normalize_terminal_text,
)
from extensions.network_operations.device_drivers import (
    resolve_driver,
    semantic_catalog,
)
from extensions.network_operations.skill_prompt import render_network_skill_prompt


@pytest.mark.parametrize("prompt", [b"\r\r\n<ASBR-PE 2>", b"\r\r\n<CE 2>", b"\r\nrouter#"])
def test_telnet_receive_idle_does_not_discard_remaining_handshake_budget(monkeypatch, prompt):
    from extensions.network_operations import device_tools
    clock = [0.0]
    class Socket:
        timeout = 2.0
        calls = 0
        def gettimeout(self): return self.timeout
        def settimeout(self, value): self.timeout = value
        def recv(self, _size):
            self.calls += 1
            if self.calls == 1:
                clock[0] += self.timeout
                raise TimeoutError()
            clock[0] += 0.2
            return prompt
    sock = Socket()
    monkeypatch.setattr(device_tools.time, "monotonic", lambda: clock[0])
    assert device_tools._telnet_read(sock, timeout=5).endswith(prompt.decode())
    assert sock.calls == 2
    assert sock.timeout == 2.0


def test_telnet_receive_bounds_socket_wait_to_remaining_deadline(monkeypatch):
    from extensions.network_operations import device_tools
    clock = [0.0]
    waits = []
    class Socket:
        timeout = 10.0
        def gettimeout(self): return self.timeout
        def settimeout(self, value): self.timeout = value
        def recv(self, _size):
            waits.append(self.timeout)
            clock[0] += self.timeout
            raise TimeoutError()
    sock = Socket()
    monkeypatch.setattr(device_tools.time, "monotonic", lambda: clock[0])
    assert device_tools._telnet_read(sock, timeout=0.25) == ""
    assert waits == [0.25]
    assert sock.timeout == 10.0


class ScriptedIO:
    def __init__(self, chunks=(), *, after_space=()):
        self.chunks = deque(chunks)
        self.after_space = list(after_space)
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        if data == b" " and self.after_space:
            self.chunks.extend(self.after_space)
            self.after_space = []

    def receive(self) -> bytes | None:
        return self.chunks.popleft() if self.chunks else None


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("LZCORE_MASTER_KEY", "test-extension-master-key")


def test_cli_runtime_handles_fragmented_pager_gb18030_and_prompt_completion():
    driver, _source = resolve_driver("h3c")
    io = ScriptedIO(
        [b"display version\r\nfirst page\r\n---- Mo", b"re ----"],
        after_space=["\r\n设备版本 7.1\r\n<CE1>".encode("gb18030")],
    )
    session = InteractiveCLISession(
        send=io.send,
        receive=io.receive,
        driver=driver,
        initial_text="<CE1>",
        timeout=1,
    )

    result = session.run_command("display version")

    assert result.complete is True
    assert result.pages == 1
    assert result.encoding == "gb18030"
    assert "设备版本 7.1" in result.output
    assert "More" not in result.output
    assert io.sent == [b"display version\r\n", b" "]


def test_cli_runtime_does_not_treat_idle_output_as_complete_without_prompt():
    driver, _source = resolve_driver("h3c")
    io = ScriptedIO([b"display current-configuration\r\npartial output"])
    session = InteractiveCLISession(
        send=io.send,
        receive=io.receive,
        driver=driver,
        initial_text="<CE1>",
        timeout=0.1,
    )

    result = session.run_command("display current-configuration")

    assert result.complete is False
    assert result.error_code == "prompt_timeout"
    assert "partial output" in result.output


def test_cli_runtime_only_accepts_prompt_on_final_nonempty_line():
    driver, _source = resolve_driver("h3c")
    io = ScriptedIO([b"display logbuffer\r\n<xml-like-output>\r\nstill running"])
    session = InteractiveCLISession(
        send=io.send,
        receive=io.receive,
        driver=driver,
        initial_text="<CE1>",
        timeout=0.1,
    )

    result = session.run_command("display logbuffer")

    assert result.complete is False
    assert result.error_code == "prompt_timeout"
    assert "still running" in result.output


def test_cli_runtime_ignores_stale_prompt_before_command_response():
    driver, _source = resolve_driver("h3c")
    io = ScriptedIO([
        b"<CE1>\r\n",
        b"display current-configuration\r\nsysname CE1\r\n<CE1>",
    ])
    session = InteractiveCLISession(
        send=io.send,
        receive=io.receive,
        driver=driver,
        initial_text="<CE1>",
        timeout=0.1,
    )

    result = session.run_command("display current-configuration")

    assert result.complete is True
    assert result.output == "sysname CE1"
    assert io.sent == [b"display current-configuration\r\n"]


def test_terminal_normalization_applies_backspaces_and_ansi_sequences():
    assert normalize_terminal_text("abc\b \bdef\x1b[31m!\x1b[0m") == "abdef!"


def test_stale_prompt_followed_by_partial_output_fences_remaining_commands():
    driver, _source = resolve_driver("h3c")
    io = ScriptedIO([b"<CE1>\r\n", b"display current-configuration\r\nsysname CE1\r\n"])
    session = InteractiveCLISession(
        send=io.send, receive=io.receive, driver=driver,
        initial_text="<CE1>", timeout=0.1,
    )
    first = session.run_command("display current-configuration")
    second = session.run_command("display version")
    assert first.complete is False
    assert "sysname CE1" in first.output
    assert second.error_code == "cli_session_unsynchronized"
    assert io.sent == [b"display current-configuration\r\n"]


def test_driver_detection_and_semantic_catalog_are_vendor_aware():
    driver, source = resolve_driver("generic", "H3C Comware Software, Version 7.1")
    assert (driver.driver_id, source) == ("h3c.comware", "observed")
    assert driver.commands_for(["device_version", "arp_table"]) == [
        ("device_version", "display version"),
        ("arp_table", "display arp"),
    ]
    version = driver.parse_facts(
        {"display version": "H3C Comware Software, Version 7.1.064, Release 0427P22"},
        {"display version": "device_version"},
    )
    assert version["device_version"]["software_version"].startswith("7.1.064")
    assert next(item for item in semantic_catalog() if item["fact"] == "device_version")["drivers"] == [
        "h3c.comware", "huawei.vrp", "cisco.ios",
    ]
    assert driver.commands_for(["bgp_peers"]) == [
        ("bgp_peers", "display bgp peer ipv4"),
        ("bgp_peers", "display bgp peer vpnv4"),
    ]
    assert driver.commands_for(["isis_neighbors", "ldp_neighbors", "mpls_lsp"]) == [
        ("isis_neighbors", "display isis peer"),
        ("ldp_neighbors", "display mpls ldp peer"),
        ("mpls_lsp", "display mpls lsp"),
    ]


def test_failed_semantic_command_is_not_reported_as_collected():
    driver, _source = resolve_driver("h3c")
    facts = driver.parse_facts(
        {"display bgp peer ipv4": "% Incomplete command"},
        {"display bgp peer ipv4": "bgp_peers"},
        [{
            "command": "display bgp peer ipv4",
            "complete": True,
            "error_code": "device_command_rejected",
            "device_error": "% Incomplete command",
        }],
    )

    assert facts["bgp_peers"]["status"] == "unavailable"
    assert facts["bgp_peers"]["failures"][0]["error_code"] == "device_command_rejected"


def test_semantic_fact_keeps_all_command_evidence():
    driver, _source = resolve_driver("h3c")
    facts = driver.parse_facts(
        {"display cpu-usage": "CPU 5%", "display memory": "Memory 40%"},
        {"display cpu-usage": "resource_usage", "display memory": "resource_usage"},
    )

    assert facts["resource_usage"]["status"] == "collected"
    assert [item["command"] for item in facts["resource_usage"]["sources"]] == [
        "display cpu-usage", "display memory",
    ]
    assert all(len(item["output_hash"]) == 64 for item in facts["resource_usage"]["sources"])
    assert facts["resource_usage"]["observation_status"] == "observed"
    assert facts["resource_usage"]["observations"][0]["literal_excerpt"] == "CPU 5%"


def test_semantic_fact_distinguishes_prompt_only_output_from_healthy_state():
    driver, _source = resolve_driver("h3c")
    facts = driver.parse_facts(
        {"display bgp peer vpnv4": "<ASBR-1>"},
        {"display bgp peer vpnv4": "bgp_peers"},
    )

    assert facts["bgp_peers"]["status"] == "collected"
    assert facts["bgp_peers"]["observation_status"] == "empty"
    assert facts["bgp_peers"]["observations"][0]["literal_excerpt"] == ""


def test_current_configuration_builds_vendor_neutral_analysis_snapshot():
    driver, _source = resolve_driver("h3c")
    config = """
sysname ASBR1
mpls lsr-id 10.0.0.1
mpls
 lsp-trigger all
bgp 65000
 peer 10.0.0.2 as-number 65001
 ipv4-family labeled-unicast
  peer 10.0.0.2 enable
route-policy EXPORT permit node 10
interface GigabitEthernet1/0/1
 mpls enable
"""

    facts = driver.parse_facts(
        {"display current-configuration": config},
        {"display current-configuration": "current_config"},
    )["current_config"]

    assert facts["status"] == "collected"
    assert facts["signal_counts"]["mpls"] >= 2
    assert facts["signal_counts"]["routing_processes"] == 1
    assert any("labeled-unicast" in line for line in facts["signals"]["address_families"])
    assert any("peer 10.0.0.2" in line for line in facts["signals"]["neighbors"])
    assert facts["sources"][0]["characters"] == len(config)


def test_current_configuration_resets_section_context_and_keeps_h3c_vpn_targets():
    driver, _source = resolve_driver("h3c")
    config = """
interface GigabitEthernet0/0
 ip address 10.0.0.1 255.255.255.0
#
bgp 100
 peer 3.3.3.9 as-number 100
#
 address-family vpnv4
  peer 5.5.5.9 enable
#
ip vpn-instance vpn1
 route-distinguisher 11:11
vpn-target 1:1 export-extcommunity
#
route-policy LABEL permit node 10
 if-match mpls-label
 apply mpls-label
"""

    facts = driver.parse_facts(
        {"display current-configuration": config},
        {"display current-configuration": "current_config"},
    )["current_config"]

    assert "[bgp 100] peer 3.3.3.9 as-number 100" in facts["signals"]["neighbors"]
    assert "[address-family vpnv4] peer 5.5.5.9 enable" in facts["signals"]["neighbors"]
    assert all("interface GigabitEthernet0/0" not in item for item in facts["signals"]["neighbors"])
    assert any("vpn-target 1:1 export-extcommunity" in item for item in facts["signals"]["vpn"])
    assert "[interface GigabitEthernet0/0] ip address 10.0.0.1 255.255.255.0" in facts["signals"]["interfaces"]
    assert "[route-policy LABEL permit node 10] if-match mpls-label" in facts["signals"]["policy"]
    assert facts["projection_complete"] is True
    assert facts["interface_addresses"] == [{
        "interface": "GigabitEthernet0/0", "address": "10.0.0.1",
        "prefix_length": 24, "network": "10.0.0.0/24",
        "configured_line": "ip address 10.0.0.1 255.255.255.0",
    }]


def test_configuration_address_normalization_preserves_different_masks_and_ipv6():
    driver, _source = resolve_driver("h3c")
    config = """interface GigabitEthernet0/0
 ip address 9.1.1.1 255.0.0.0
 ipv6 address 2001:db8::1/64
#
interface GigabitEthernet0/1
 ip address 9.1.1.2 255.255.255.0
#
role name level-0
 description Not an interface description
"""
    fact = driver.parse_facts(
        {"display current-configuration": config},
        {"display current-configuration": "current_config"},
    )["current_config"]
    assert [item["prefix_length"] for item in fact["interface_addresses"]] == [8, 64, 24]
    assert all("Not an interface" not in line for line in fact["signals"]["interfaces"])


def test_semantic_collect_persists_detected_profile_and_returns_facts(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "CE1", "host": "10.0.0.1", "vendor": "h3c"})
    captured = {}

    def fake_probe(_target, **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "succeeded",
            "duration_ms": 4,
            "facts": {"device_version": {"software_version": "7.1"}},
            "device_profile": {
                "driver_id": "h3c.comware",
                "vendor": "h3c",
                "os_family": "comware",
                "detected_from": "observed",
                "semantic_facts": ["device_version", "interface_status"],
            },
        }

    monkeypatch.setattr(service, "probe_target", fake_probe)
    connection = service.save_connection(
        "default",
        {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"},
        auto_test=False,
    )
    skill = service.save_skill("default", {
        "name": "设备巡检",
        "device_ids": [device["device_id"]],
        "connection_ids": [connection["connection_id"]],
    })

    result = device_manage(SimpleNamespace(
        workspace_id="default",
        skill=skill["skill_id"],
        arguments={
            "action": "collect",
            "connection_id": connection["connection_id"],
            "facts": ["device_version"],
        },
    ))

    assert result["facts"]["device_version"]["software_version"] == "7.1"
    assert captured["facts"] == ["device_version"]
    assert captured["commands"] == []
    persisted = service.get_connection("default", connection["connection_id"])
    assert persisted["driver_id"] == "h3c.comware"
    assert persisted["semantic_facts"] == ["device_version", "interface_status"]


def test_multi_device_semantic_inspection_keeps_fact_plan(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    h3c = service.save_device("default", {"name": "H3C", "host": "10.0.0.1", "vendor": "h3c"})
    cisco = service.save_device("default", {"name": "Cisco", "host": "10.0.0.2", "vendor": "cisco"})
    monkeypatch.setattr(service, "probe_target", lambda *_args, **_kwargs: {"ok": True})
    first = service.save_connection("default", {"device_id": h3c["device_id"], "protocol": "telnet", "auth_method": "none"}, auto_test=False)
    second = service.save_connection("default", {"device_id": cisco["device_id"], "protocol": "telnet", "auth_method": "none"}, auto_test=False)

    task, _targets, _script = service._new_connection_inspection_task(
        "default",
        [first["connection_id"], second["connection_id"]],
        None,
        "",
        facts=["device_version", "interface_status"],
    )

    assert task["command_plan"] == {
        "mode": "semantic_facts",
        "facts": ["device_version", "interface_status"],
    }
    assert service._restore_command_plan(task) == (
        None, None, ["device_version", "interface_status"],
    )


def test_semantic_inspection_uses_live_runtime_and_preserves_partial_evidence(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "CE1", "host": "10.0.0.1", "vendor": "generic"})
    connection = service.save_connection(
        "default",
        {"device_id": device["device_id"], "protocol": "telnet", "auth_method": "none"},
        auto_test=False,
    )
    task, targets, script = service._new_connection_inspection_task(
        "default", [connection["connection_id"]], None, "", facts=["resource_usage"],
    )
    service._store("default").save("inspections", task["task_id"], task)

    def fake_live(_workspace_id, connection_id, **kwargs):
        assert connection_id == connection["connection_id"]
        assert kwargs["facts"] == ["resource_usage"]
        return {
            "ok": True,
            "read_ok": False,
            "output": {"display cpu-usage": "CPU 5%"},
            "facts": {"resource_usage": {"status": "collected"}},
            "command_results": [{
                "command": "display cpu-usage", "fact": "resource_usage",
                "complete": True, "error_code": "", "device_error": "",
            }, {
                "command": "display memory", "fact": "resource_usage",
                "complete": False, "error_code": "prompt_timeout", "device_error": "",
            }],
        }

    monkeypatch.setattr(service, "test_connection", fake_live)
    service._execute_inspection(
        "default", task["task_id"], targets, None,
        lambda *_args: (_ for _ in ()).throw(AssertionError("raw collector must not run")),
        Event(), script, ["resource_usage"],
    )
    finished = service.get_inspection("default", task["task_id"])

    assert finished["status"] == "partial"
    assert finished["partial"] == 1
    result = finished["results"][connection["connection_id"]]
    assert result["status"] == "partial"
    assert result["facts"]["resource_usage"]["status"] == "collected"
    assert result["command_results"][1]["error_code"] == "prompt_timeout"


def test_selected_skill_prompt_explains_semantic_collect_without_raw_pager_commands():
    prompt = render_network_skill_prompt({
        "skill_id": "skill_1",
        "connections": [{"driver_id": "h3c.comware", "semantic_facts": ["device_version"]}],
        "semantic_catalog": semantic_catalog(),
        "network_runtime_version": "network.cli.v2",
    })
    assert 'action="collect"' in prompt
    assert "Never send paging-disable commands yourself" in prompt
    assert '"network_runtime_version":"network.cli.v2"' in prompt
