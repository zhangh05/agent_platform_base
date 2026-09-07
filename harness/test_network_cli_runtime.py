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
from extensions.network_operations.device_tools import is_read_only_command
from extensions.network_operations.device_drivers import (
    resolve_driver,
    semantic_catalog,
)
from extensions.network_operations.skill_prompt import render_network_skill_prompt


def test_config_prompt_is_builtin_for_every_selected_network_skill():
    prompt = render_network_skill_prompt({"skill_id": "test"})
    assert "Device execution contract" in prompt
    assert "fresh shell" in prompt
    assert "Configuration write capability is enabled" not in prompt


@pytest.mark.parametrize("commands,vendor", [([], "h3c"), ([42], "h3c")])
def test_configuration_rejects_only_missing_or_non_string_payloads(commands, vendor):
    from extensions.network_operations.device_tools import normalize_configuration_commands
    with pytest.raises(ValueError):
        normalize_configuration_commands(commands, vendor)


@pytest.mark.parametrize("failure", ["device_command_rejected", "command_dispatch_uncertain", "interaction_required", "execution_timeout"])
def test_configuration_collects_every_command_result_after_a_failure(failure):
    from extensions.network_operations.device_tools import _execute_commands, _Connection
    from extensions.network_operations.cli_runtime import CLICommandResult
    driver, _ = resolve_driver("h3c", "<CE>")
    calls = []
    class Session:
        prompt, encoding, synchronized = "<CE>", "utf-8", True
        def run_command(self, command):
            calls.append(command)
            rejected = failure == "device_command_rejected"
            return CLICommandResult(command, "error", self.prompt, rejected, 0, self.encoding, 0,
                                    error_code=failure, dispatch_status="uncertain" if failure == "command_dispatch_uncertain" else "sent")
    session = Session()
    session.driver = driver
    conn = _Connection(session, lambda: None, [], paging_initialized=True)
    result = _execute_commands(conn, ["system-view", "interface LoopBack 100", "return"], None, read=False, configure=True)
    assert calls == ["system-view", "interface LoopBack 100", "return"]
    assert result["unexecuted_commands"] == []
    assert [item["command"] for item in result["command_results"]] == calls
    assert result["execution_may_continue"] is (failure != "device_command_rejected")
    assert not result["ok"] and not result["automatic_retry_allowed"]
    assert result["recommended_readback"] and not result["rollback_performed"]


def test_configuration_marks_only_unsendable_remainder_after_transport_exception():
    from extensions.network_operations.device_tools import _execute_commands, _Connection
    from extensions.network_operations.cli_runtime import CLICommandResult
    driver, _ = resolve_driver("h3c", "<CE>")
    calls = []

    class Session:
        prompt, encoding, synchronized = "<CE>", "utf-8", True

        def run_command(self, command):
            calls.append(command)
            if command == "system-view":
                raise OSError("transport disconnected")
            return CLICommandResult(command, "ok", self.prompt, True, 0, self.encoding, 0, dispatch_status="sent")

        def invalidate(self):
            self.synchronized = False

    session = Session()
    session.driver = driver
    result = _execute_commands(
        _Connection(session, lambda: None, [], paging_initialized=True),
        ["system-view", "interface LoopBack 100", "return"], None,
        read=False, configure=True,
    )
    assert calls == ["system-view"]
    assert [item["dispatch_status"] for item in result["command_results"]] == ["uncertain", "not_sent", "not_sent"]
    assert result["unexecuted_commands"] == ["interface LoopBack 100", "return"]


def test_configuration_preserves_repeated_lines_and_exact_model_commands():
    from extensions.network_operations.device_tools import normalize_configuration_commands, normalize_read_only_commands
    commands = ["system-view", "interface LoopBack 1", "description test", "quit", "interface LoopBack 2", "description test", "return"]
    assert normalize_configuration_commands(commands, "h3c") == commands
    with pytest.raises(ValueError):
        normalize_read_only_commands(commands, "h3c")


def test_retained_telnet_configuration_view_is_reset_before_reading():
    from extensions.network_operations.device_tools import _execute_commands, _Connection
    from extensions.network_operations.cli_runtime import CLICommandResult
    driver, _ = resolve_driver("h3c", "[CE-interface]")
    calls = []
    class Session:
        prompt, encoding, synchronized = "[CE-interface]", "utf-8", True
        def run_command(self, command, *, internal=False):
            calls.append((command, internal))
            self.prompt = "<CE>"
            return CLICommandResult(command, "ok", self.prompt, True, 0, self.encoding, 0, dispatch_status="sent")
    session = Session()
    session.driver = driver
    result = _execute_commands(_Connection(session, lambda: None, [], paging_initialized=True), ["display version"], None, read=True)
    assert calls == [("return", True), ("display version", False)]
    assert result["read_ok"] and result["session"]["mode_reset"]["command"] == "return"


def test_configuration_uses_exact_commands_and_disposes_each_cli_session(monkeypatch):
    from extensions.network_operations import device_tools as tools
    driver, _ = resolve_driver("h3c", "<CE>")
    sent, closed = [], []
    def connect(*_args):
        queue = deque([b"<CE>"])
        def send(data):
            sent.append(data)
            queue.append(b"Done\r\n<CE>" if data == b"return\r\n" else b"Done\r\n[CE]")
        session = InteractiveCLISession(send=send, receive=lambda: queue.popleft() if queue else None, driver=driver, timeout=1)
        assert session.bootstrap().complete
        return tools._Connection(session, lambda: closed.append(True), [], paging_initialized=True)
    monkeypatch.setattr(tools, "_open_connection", connect)
    target = tools.DeviceTarget("127.0.0.1", 23, "telnet", "h3c")
    commands = ["system-view", "sysname CE", "return"]
    for _ in range(2):
        result = tools.probe_target(target, commands=commands, configure=True, session_key="same-task", timeout=2)
        assert result["configuration_ok"] and result["ok"]
        assert result["session"]["scope"] == "operation" and not result["session"]["reused"]
        assert result["unexecuted_commands"] == []
    assert sent == ([b"\r\n"] + [(line + "\r\n").encode() for line in commands]) * 2
    assert len(closed) == 2


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


def test_cli_runtime_accepts_prompt_followed_only_by_async_console_notice():
    driver, _source = resolve_driver("h3c")
    io = ScriptedIO([
        b"display interface brief\r\nGE0/0 UP UP\r\n<PE 1>\r\n"
        b"<PE 1>%Sep  1 17:06:17:431 2026 PE 1 SHELL/5/SHELL_LOGIN: Console logged in"
    ])
    session = InteractiveCLISession(
        send=io.send,
        receive=io.receive,
        driver=driver,
        initial_text="<PE 1>",
        timeout=0.1,
    )

    result = session.run_command("display interface brief")

    assert result.complete is True
    assert result.error_code == ""
    assert result.prompt == "<PE 1>"
    assert "GE0/0 UP UP" in result.output


def test_cli_runtime_settles_a_late_async_notice_before_next_command():
    """A split console notice must not become the next command's output."""
    driver, _source = resolve_driver("h3c")
    io = ScriptedIO([
        b"display interface brief\r\nGE0/0 UP UP\r\n<PE 1>",
        b"\r\n%Sep  1 17:06:17:431 2026 PE 1 SHELL/5/SHELL_LOGIN: Console logged in",
    ])
    session = InteractiveCLISession(
        send=io.send, receive=io.receive, driver=driver,
        initial_text="<PE 1>", timeout=1,
    )

    result = session.run_command("display interface brief")

    assert result.complete is True
    assert result.output == "GE0/0 UP UP"
    assert result.async_notices == [
        "%Sep  1 17:06:17:431 2026 PE 1 SHELL/5/SHELL_LOGIN: Console logged in"
    ]
    assert not io.chunks


def test_cli_runtime_does_not_complete_when_ordinary_output_arrives_after_prompt():
    driver, _source = resolve_driver("h3c")
    io = ScriptedIO([b"display logbuffer\r\n<PE 1>", b"\r\nstill running"])
    session = InteractiveCLISession(
        send=io.send, receive=io.receive, driver=driver,
        initial_text="<PE 1>", timeout=0.1,
    )

    result = session.run_command("display logbuffer")

    assert result.complete is False
    assert result.error_code == "prompt_timeout"


@pytest.mark.parametrize("notice", [
    b"<PE 1>%Sep  1 17:06:17:431 2026 PE 1 SHELL/5/SHELL_LOGIN: Console logged in",
    b"%Sep  1 17:06:17:431 2026 PE 1 SHELL/5/SHELL_LOGIN: Console logged in",
])
def test_cli_runtime_accepts_both_comware_async_notice_forms(notice):
    driver, _source = resolve_driver("h3c")
    io = ScriptedIO([
        b"return\r\n<PE 1>\r\n" + notice,
    ])
    session = InteractiveCLISession(
        send=io.send,
        receive=io.receive,
        driver=driver,
        initial_text="[PE 1-if]",
        timeout=0.1,
    )

    result = session.run_command("return")

    assert result.complete is True
    assert result.error_code == ""
    assert result.prompt == "<PE 1>"


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
        service.collect_connection,
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


def test_prompt_makes_autonomous_commands_the_default_not_templates():
    prompt = render_network_skill_prompt({})
    assert 'use action="read"' in prompt
    assert "There are no implicit default commands" in prompt
    assert "No framework script decides your diagnostic sequence" in prompt
    assert "only when the semantic catalog cannot" not in prompt


@pytest.mark.parametrize("command", [
    "ping -vpn-instance vpn1 10.0.0.1", "tracert 10.0.0.1",
    "ping vrf blue 10.0.0.1 repeat 5",
])
def test_ping_is_read_only_but_other_diagnostics_require_approval(command):
    expected = command.startswith("ping ")
    assert is_read_only_command(command, "h3c" if "vrf " not in command else "cisco") is expected


@pytest.mark.parametrize("command", [
    "ping 10.0.0.1 repeat 999", "ping -c 999 10.0.0.1",
    "ping -unknown value 10.0.0.1",
])
def test_ping_arguments_do_not_change_its_read_class(command):
    assert is_read_only_command(command, "h3c")


def test_command_chaining_is_not_a_single_read_command():
    assert not is_read_only_command("ping 10.0.0.1 && reboot", "h3c")


def test_raw_read_without_commands_never_contacts_device(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "R1", "host": "10.0.0.1", "vendor": "h3c"})
    connection = service.save_connection("default", {
        "device_id": device["device_id"], "protocol": "telnet", "auth_method": "none",
    }, auto_test=False)
    monkeypatch.setattr(service, "probe_target", lambda *_a, **_k: pytest.fail("implicit device IO"))
    for commands in (None, [], "display version"):
        result = device_manage(SimpleNamespace(workspace_id="default", arguments={
            "action": "read", "connection_id": connection["connection_id"], "commands": commands,
        }))
        assert result["ok"] is False
    result = service.test_connection("default", connection["connection_id"], read=True)
    assert result["ok"] is False


def test_telnet_negotiation_is_incremental_and_negative_replies_do_not_loop():
    from extensions.network_operations.device_tools import _TelnetDecoder
    sent = []
    decoder = _TelnetDecoder(SimpleNamespace(sendall=sent.append))
    assert decoder.feed(b"a\xff") == b"a"
    assert decoder.feed(b"\xfb") == b""
    assert decoder.feed(b"\x01b\xff\xfa\x18") == b"b"
    assert decoder.feed(b"hidden-option\xff") == b""
    assert decoder.feed(b"\xf0c\xff\xfc\x01\xff\xfe\x03") == b"c"
    assert sent == [b"\xff\xfe\x01"]
    assert decoder.feed(b"\xff\xff") == b"\xff"


def test_nonpaging_confirmation_never_receives_an_automatic_answer():
    driver, _ = resolve_driver("h3c")
    io = ScriptedIO([b"display something\r\nContinue? [Y/N]"])
    session = InteractiveCLISession(send=io.send, receive=io.receive, driver=driver, initial_text="<CE1>")
    first = session.run_command("display something")
    second = session.run_command("display version")
    assert first.error_code == "interaction_required"
    assert not first.complete
    assert second.error_code == "cli_session_unsynchronized"
    assert io.sent == [b"display something\r\n"]


@pytest.mark.parametrize("failure", ["disconnect", "timeout", "cancel"])
def test_incomplete_command_does_not_send_the_next_command(monkeypatch, failure):
    from extensions.network_operations import cli_runtime
    from core.tools.context import bind_runtime_cancel_check, reset_runtime_cancel_check
    driver, _ = resolve_driver("h3c")
    chunks = [b"display version\r\npartial\r\n"]
    if failure == "disconnect":
        chunks.append(b"")
    io = ScriptedIO(chunks)
    session = InteractiveCLISession(send=io.send, receive=io.receive, driver=driver, initial_text="<CE1>")
    import time
    session.deadline = time.monotonic() + 0.1
    token = bind_runtime_cancel_check(lambda: failure == "cancel" and bool(io.sent))
    try:
        first = session.run_command("display version")
        second = session.run_command("display interface brief")
    finally:
        reset_runtime_cancel_check(token)
    assert not first.complete
    assert second.error_code == {"timeout": "execution_timeout", "cancel": "cancelled"}.get(failure, "cli_session_unsynchronized")
    assert io.sent == [b"display version\r\n"]


@pytest.mark.parametrize("protocol", ["ssh", "telnet"])
def test_shared_executor_reuses_task_session_and_sends_exact_model_commands(monkeypatch, protocol):
    from extensions.network_operations import device_tools
    pool = device_tools._SessionPool(ttl=60)
    monkeypatch.setattr(device_tools, "_SESSIONS", pool)
    opens, sent, closes = [], [], []
    def open_connection(target, timeout, accept_host_key):
        opens.append(target.protocol)
        chunks = deque()
        def send(data):
            sent.append(data)
            command = data.decode().strip()
            chunks.append(data + (b"literal-evidence\r\n" if command.startswith("display ") else b"") + b"<CE1>")
        driver, _ = resolve_driver("h3c")
        cli = InteractiveCLISession(send=send, receive=lambda: chunks.popleft() if chunks else None,
                                    driver=driver, initial_text="<CE1>")
        return device_tools._Connection(cli, lambda: closes.append(True), [])
    monkeypatch.setattr(device_tools, "_open_connection", open_connection)
    target = device_tools.DeviceTarget("10.0.0.1", protocol=protocol, vendor="h3c")
    try:
        first = device_tools.probe_target(target, commands=["display interface GigabitEthernet1/0/1"],
                                         read=True, session_key="trusted-task")
        second = device_tools.probe_target(target, commands=["display ip routing-table 10.1.1.1"],
                                          read=True, session_key="trusted-task")
        assert first["read_ok"] and second["read_ok"]
        assert not first["session"]["reused"] and second["session"]["reused"]
        assert opens == [protocol]
        assert [d.decode().strip() for d in sent if d.startswith(b"display ")] == [
            "display interface GigabitEthernet1/0/1", "display ip routing-table 10.1.1.1",
        ]
        assert second["command_source"] == "explicit_commands"
        assert sent.count(b"screen-length disable\r\n") <= 1
        # Expiration/disconnect is checked before dispatch, not by replaying
        # an already-sent diagnostic command.
        pool.entries["trusted-task"]["connection"].session.invalidate()
        third = device_tools.probe_target(target, commands=["display device"], read=True, session_key="trusted-task")
        assert third["read_ok"] and not third["session"]["reused"]
        assert sent.count(b"display device\r\n") == 1
        assert len(opens) == 2
        # A new task cannot inherit another task's CLI stream.
        device_tools.probe_target(target, commands=["display version"], read=True, session_key="another-task")
        assert len(opens) == 3
    finally:
        pool.close_all()
    assert len(closes) == 3


def test_session_pool_expires_idle_and_does_not_expire_active_handles(monkeypatch):
    from extensions.network_operations.device_tools import _SessionPool
    pool = _SessionPool(ttl=60, limit=1)
    closed = []
    handle = SimpleNamespace(close=lambda: closed.append(True), session=SimpleNamespace(synchronized=True))
    with pool.lease("a") as entry:
        entry["connection"] = handle
        pool._expire("a", entry)
        assert not closed
        with pytest.raises(RuntimeError, match="busy"):
            with pool.lease("a"):
                pass
        with pytest.raises(RuntimeError, match="capacity"):
            with pool.lease("b"):
                pass
    pool._expire("a", entry)
    assert closed == [True] and not pool.entries


def test_receive_failure_preserves_partial_evidence_and_send_failure_is_uncertain():
    driver, _ = resolve_driver("h3c")
    io = ScriptedIO([b"display version\r\nretained evidence\r\n"])
    def receive():
        if io.chunks:
            return io.receive()
        raise ConnectionResetError()
    session = InteractiveCLISession(send=io.send, receive=receive, driver=driver, initial_text="<CE1>")
    result = session.run_command("display version")
    assert result.output == "retained evidence"
    assert result.error_code == "connection_closed"
    assert result.dispatch_status == "sent"
    def failed_send(_data):
        raise BrokenPipeError()
    session = InteractiveCLISession(send=failed_send, receive=io.receive, driver=driver, initial_text="<CE1>")
    result = session.run_command("display version")
    assert result.error_code == "command_dispatch_uncertain"
    assert result.dispatch_status == "uncertain"
    assert session.run_command("display interface brief").dispatch_status == "not_sent"


def test_raw_inspection_preserves_incomplete_command_diagnostics(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    device = service.save_device("default", {"name": "CE1", "host": "10.0.0.1", "vendor": "h3c"})
    connection = service.save_connection("default", {
        "device_id": device["device_id"], "protocol": "telnet", "auth_method": "none",
    }, auto_test=False)
    task, targets, script = service._new_connection_inspection_task(
        "default", [connection["connection_id"]], ["display current-configuration"], "",
    )
    service._store("default").save("inspections", task["task_id"], task)
    monkeypatch.setattr(service, "probe_target", lambda *_a, **_k: {
        "ok": True, "read_ok": False, "output": {"display current-configuration": "partial config"},
        "command_results": [{"command": "display current-configuration", "complete": False,
                             "error_code": "connection_closed", "truncated": False}],
    })
    service._execute_inspection("default", task["task_id"], targets, ["display current-configuration"],
                                service.collect_connection, Event(), script)
    result = service.get_inspection("default", task["task_id"])
    assert result["status"] == "partial"
    assert result["results"][connection["connection_id"]]["command_results"][0]["error_code"] == "connection_closed"
