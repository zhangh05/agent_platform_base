from __future__ import annotations

from collections import deque
from types import SimpleNamespace

from extensions.network_operations import service
from extensions.network_operations.backend import device_manage
from extensions.network_operations.cli_runtime import (
    InteractiveCLISession,
    normalize_terminal_text,
)
from extensions.network_operations.device_drivers import resolve_driver, semantic_catalog
from extensions.network_operations.skill_prompt import render_network_skill_prompt


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


def test_terminal_normalization_applies_backspaces_and_ansi_sequences():
    assert normalize_terminal_text("abc\b \bdef\x1b[31m!\x1b[0m") == "abdef!"


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
        service.threading.Event(), script, ["resource_usage"],
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
