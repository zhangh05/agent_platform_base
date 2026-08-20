"""Real Docker isolation checks. Run only from the dedicated Linux CI job."""

from __future__ import annotations

import os

import pytest

from core.tools.python_runner import DockerStrongIsolationRunner

pytestmark = pytest.mark.skipif(
    os.environ.get("LZCORE_RUN_DOCKER_INTEGRATION") != "1",
    reason="real Docker integration is opt-in",
)


def _runner() -> DockerStrongIsolationRunner:
    runner = DockerStrongIsolationRunner.available()
    assert runner is not None, "pinned Docker runner must be available"
    return runner


def test_real_container_executes_with_declared_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    code = """
result = {"sum": sum(input_data["values"])}
"""
    result = _runner().execute(
        code=code,
        workspace_id="docker_integration",
        run_id="isolation",
        timeout=5,
        input_data={"values": [1, 2, 3]},
    )
    assert result["ok"] is True
    assert result["structured_output"] == {"sum": 6}
    assert result["network"] == "none"
    assert result["isolation_level"] == "strong_container"
    assert result["resource_limits"] == {
        "memory": "128m",
        "cpus": "0.5",
        "pids": 16,
        "output_bytes": 1_048_576,
    }
    assert not list(tmp_path.rglob("script.py"))


def test_network_and_host_write_code_is_rejected_before_container(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    runner = _runner()
    network = runner.execute(
        code="import socket\nsocket.create_connection(('1.1.1.1', 53))",
        workspace_id="docker_integration",
        run_id="network",
        timeout=2,
        input_data={},
    )
    host_write = runner.execute(
        code="open('/workspace/host-write', 'w').write('escape')",
        workspace_id="docker_integration",
        run_id="write",
        timeout=2,
        input_data={},
    )
    assert "Forbidden import" in network["error"]
    assert "Forbidden function call" in host_write["error"]
    assert not list(tmp_path.rglob("host-write"))


def test_timeout_force_removes_container(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    result = _runner().execute(
        code="while True: pass",
        workspace_id="docker_integration",
        run_id="timeout",
        timeout=1,
        input_data={},
    )
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert not list((tmp_path / "workspaces").rglob("script.py"))
