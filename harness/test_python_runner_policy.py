"""Focused tests for Python runner selection and fail-closed behavior."""
from __future__ import annotations

import subprocess

from core.tools.python_runner import (
    BestEffortPythonRunner,
    DockerStrongIsolationRunner,
    UnavailableStrongIsolationRunner,
    select_python_runner,
)


def _without_docker(monkeypatch):
    monkeypatch.setattr(DockerStrongIsolationRunner, "available", staticmethod(lambda: None))


def test_non_loopback_mode_requires_strong_runner(monkeypatch):
    _without_docker(monkeypatch)
    monkeypatch.setenv("LZCORE_RUNTIME_BIND_HOST", "0.0.0.0")
    monkeypatch.delenv("LZCORE_TRUSTED_LOCAL_PYTHON_EXECUTION", raising=False)
    runner = select_python_runner()
    assert isinstance(runner, UnavailableStrongIsolationRunner)
    result = runner.execute(code="print(1)", workspace_id="ws", run_id="r", timeout=1, input_data={})
    assert result["ok"] is False
    assert result["isolation_level"] == "unavailable"
    assert "requires_strong_isolation" in result["error"]


def test_trusted_local_opt_in_uses_best_effort_runner(monkeypatch):
    _without_docker(monkeypatch)
    monkeypatch.setenv("LZCORE_RUNTIME_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LZCORE_TRUSTED_LOCAL_PYTHON_EXECUTION", "true")
    assert isinstance(select_python_runner(), BestEffortPythonRunner)


def test_identity_mode_requires_strong_runner_even_on_loopback(monkeypatch):
    _without_docker(monkeypatch)
    monkeypatch.setenv("LZCORE_RUNTIME_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("LZCORE_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("LZCORE_TRUSTED_LOCAL_PYTHON_EXECUTION", "true")
    assert isinstance(select_python_runner(), UnavailableStrongIsolationRunner)


def test_container_runner_requires_operator_pinned_image(monkeypatch):
    monkeypatch.delenv("LZCORE_PYTHON_CONTAINER_IMAGE", raising=False)
    monkeypatch.delenv("LZCORE_ALLOW_MUTABLE_PYTHON_IMAGE", raising=False)
    assert DockerStrongIsolationRunner.available() is None


def test_container_runner_requires_pinned_image_to_exist_locally(monkeypatch):
    image = "python@example.invalid@sha256:" + "a" * 64
    monkeypatch.setenv("LZCORE_PYTHON_CONTAINER_IMAGE", image)
    monkeypatch.setattr("core.tools.python_runner.shutil.which", lambda _name: "/usr/bin/docker")

    def fake_run(command, **_kwargs):
        if command[1] == "version":
            return subprocess.CompletedProcess(command, 0, stdout="26.1.0\n", stderr="")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="No such image")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert DockerStrongIsolationRunner.available() is None


def test_container_runner_cleans_host_script_after_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    runner = DockerStrongIsolationRunner(
        docker_bin="/usr/bin/docker",
        image="python@example.invalid@sha256:" + "a" * 64,
    )

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="done\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.execute(
        code="print('done')",
        workspace_id="cleanup_ws",
        run_id="cleanup_run",
        timeout=2,
        input_data={"private": "value"},
    )
    assert result["ok"] is True
    temp_root = tmp_path / "cleanup_ws" / "files" / "tmp" / "python_exec"
    assert not temp_root.exists() or list(temp_root.iterdir()) == []
