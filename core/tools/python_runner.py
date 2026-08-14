"""Execution runners for ``exec.run(action=python)``.

The local subprocess implementation is intentionally *best effort* only.  A
network-exposed or multi-user runtime must use the Docker-backed runner or fail
closed; this module is the single selection point for that invariant.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from storage.workspace_files import write_python_temp_script

_MAX_INPUT_BYTES = 1_048_576
_MAX_CODE_BYTES = 1_048_576
_MAX_OUTPUT_BYTES = 1_048_576
_CONTAINER_IMAGE_ENV = "LZCORE_PYTHON_CONTAINER_IMAGE"


def _docker_client_env() -> dict[str, str]:
    allowed = ("PATH", "DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
    return {key: os.environ[key] for key in allowed if os.environ.get(key)}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _loopback_host(host: str) -> bool:
    return host.strip().strip("[]") in {"localhost", "::1"} or host.strip().startswith("127.")


def _requires_strong_isolation() -> bool:
    host = os.environ.get("LZCORE_RUNTIME_BIND_HOST", "127.0.0.1")
    if not _loopback_host(host):
        return True
    if _truthy(os.environ.get("LZCORE_IDENTITY_ENABLED")):
        return True
    if os.environ.get("LZCORE_LOGIN_USERNAME", "").strip() and os.environ.get(
        "LZCORE_LOGIN_PASSWORD", ""
    ):
        return True
    return _truthy(os.environ.get("LZCORE_PYTHON_REQUIRE_STRONG_ISOLATION"))


def _empty_result(timeout: int, error: str, isolation_level: str, *, runner: str) -> dict[str, Any]:
    return {
        "ok": False,
        "exit_code": -1,
        "stdout": "",
        "stderr": "",
        "timeout_seconds": timeout,
        "error": error,
        "isolation_level": isolation_level,
        "runner": runner,
    }


class PythonRunner(Protocol):
    isolation_level: str
    runner_name: str

    def execute(
        self,
        *,
        code: str,
        workspace_id: str,
        run_id: str,
        timeout: int,
        input_data: Any,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BestEffortPythonRunner:
    isolation_level: str = "best_effort"
    runner_name: str = "local_subprocess"

    def execute(self, *, code: str, workspace_id: str, run_id: str, timeout: int, input_data: Any) -> dict[str, Any]:
        from core.tools.python_exec import execute_best_effort_python_code

        result = execute_best_effort_python_code(
            code=code,
            workspace_id=workspace_id,
            run_id=run_id,
            timeout=timeout,
            input_data=input_data,
        )
        result.update(
            {
                "isolation_level": self.isolation_level,
                "runner": self.runner_name,
                "security_notice": "best_effort_local_only_not_a_sandbox",
            }
        )
        return result


@dataclass(frozen=True)
class UnavailableStrongIsolationRunner:
    reason: str
    isolation_level: str = "unavailable"
    runner_name: str = "docker"

    def execute(self, *, code: str, workspace_id: str, run_id: str, timeout: int, input_data: Any) -> dict[str, Any]:
        del code, workspace_id, run_id, input_data
        return _empty_result(
            timeout,
            f"python_execution_requires_strong_isolation: {self.reason}",
            self.isolation_level,
            runner=self.runner_name,
        )


@dataclass(frozen=True)
class DockerStrongIsolationRunner:
    docker_bin: str
    image: str
    isolation_level: str = "strong_container"
    runner_name: str = "docker"

    @classmethod
    def available(cls) -> "DockerStrongIsolationRunner | None":
        image = os.environ.get(_CONTAINER_IMAGE_ENV, "").strip()
        allow_mutable = _truthy(os.environ.get("LZCORE_ALLOW_MUTABLE_PYTHON_IMAGE"))
        if not image or ("@sha256:" not in image and not allow_mutable):
            return None
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return None
        probe = subprocess.run(
            [docker_bin, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=_docker_client_env(),
        )
        if probe.returncode != 0 or not probe.stdout.strip():
            return None
        image_probe = subprocess.run(
            [docker_bin, "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=_docker_client_env(),
        )
        if image_probe.returncode != 0 or not image_probe.stdout.strip():
            return None
        return cls(docker_bin=docker_bin, image=image)

    def execute(self, *, code: str, workspace_id: str, run_id: str, timeout: int, input_data: Any) -> dict[str, Any]:
        prepared = _prepare_execution(code=code, workspace_id=workspace_id, run_id=run_id, input_data=input_data, timeout=timeout)
        if isinstance(prepared, dict):
            prepared.update({"isolation_level": self.isolation_level, "runner": self.runner_name})
            return prepared
        temp_dir, script_path = prepared
        container_name = f"lzcore-python-{uuid.uuid4().hex[:16]}"
        # Host secrets are not passed to Docker. The script mount is read-only;
        # a tiny tmpfs is the sole writable filesystem inside the container.
        command = [
            self.docker_bin,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "16",
            "--memory",
            "128m",
            "--cpus",
            "0.5",
            "--ulimit",
            "fsize=2097152:2097152",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16m",
            "--mount",
            f"type=bind,src={temp_dir},dst=/workspace,ro",
            self.image,
            "sh",
            "-c",
            "ulimit -f 2048; python /workspace/" + script_path.name + " > /tmp/stdout.txt 2> /tmp/stderr.txt; status=$?; cat /tmp/stdout.txt; cat /tmp/stderr.txt >&2; exit $status",
        ]
        try:
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout + 5,
                    check=False,
                    env={"PATH": os.environ.get("PATH", "")},
                )
            except subprocess.TimeoutExpired:
                # Killing the named container is required because killing the Docker
                # client alone does not prove that the container process tree ended.
                subprocess.run(
                    [self.docker_bin, "rm", "--force", container_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    env=_docker_client_env(),
                )
                result = _empty_result(
                    timeout,
                    f"Execution timed out after {timeout}s; container removed",
                    self.isolation_level,
                    runner=self.runner_name,
                )
                result["timed_out"] = True
                return result
            stdout = (completed.stdout or "")[:_MAX_OUTPUT_BYTES]
            stderr = (completed.stderr or "")[:_MAX_OUTPUT_BYTES]
            output_truncated = len(completed.stdout or "") > _MAX_OUTPUT_BYTES or len(completed.stderr or "") > _MAX_OUTPUT_BYTES
            result = {
                "ok": completed.returncode == 0,
                "exit_code": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "structured_output": _extract_structured_output(stdout),
                "timeout_seconds": timeout,
                "error": "" if completed.returncode == 0 else (stderr.strip() or f"Container Python exited with code {completed.returncode}"),
                "isolation_level": self.isolation_level,
                "runner": self.runner_name,
                "network": "none",
                "resource_limits": {"memory": "128m", "cpus": "0.5", "pids": 16, "output_bytes": _MAX_OUTPUT_BYTES},
            }
            if output_truncated:
                result["output_truncated"] = True
            return result
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
def select_python_runner() -> PythonRunner:
    if _requires_strong_isolation():
        runner = DockerStrongIsolationRunner.available()
        if runner is not None:
            return runner
        return UnavailableStrongIsolationRunner("docker runner unavailable")
    if _truthy(os.environ.get("LZCORE_TRUSTED_LOCAL_PYTHON_EXECUTION")):
        return BestEffortPythonRunner()
    runner = DockerStrongIsolationRunner.available()
    if runner is not None:
        return runner
    return UnavailableStrongIsolationRunner("trusted-local opt-in required")


def _prepare_execution(*, code: str, workspace_id: str, run_id: str, input_data: Any, timeout: int) -> tuple[Path, Path] | dict[str, Any]:
    from core.tools.python_exec import PythonExecSecurityError, validate_code

    if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", workspace_id):
        return _empty_result(timeout, "invalid_workspace_id", "strong_container", runner="docker")
    if len(code.encode("utf-8")) > _MAX_CODE_BYTES:
        return _empty_result(timeout, "python code exceeds 1 MiB", "strong_container", runner="docker")
    try:
        validate_code(code)
    except PythonExecSecurityError as exc:
        return _empty_result(timeout, f"Security check failed: {exc}", "strong_container", runner="docker")
    try:
        input_json = json.dumps(input_data if input_data is not None else {}, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        return _empty_result(timeout, f"input_data is not JSON serializable: {exc}", "strong_container", runner="docker")
    if len(input_json.encode("utf-8")) > _MAX_INPUT_BYTES:
        return _empty_result(timeout, "input_data exceeds 1 MiB", "strong_container", runner="docker")
    safe_run_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(run_id) or "unknown") or "unknown"
    safe_run_id = f"{safe_run_id[:40]}_{uuid.uuid4().hex[:12]}"
    preamble = "import json as _runtime_json\n" + f"input_data = _runtime_json.loads({input_json!r})\n"
    postamble = "\ntry:\n    _runtime_structured = result\nexcept NameError:\n    _runtime_structured = None\nif _runtime_structured is not None:\n    print('__LIANZHI_STRUCTURED__' + _runtime_json.dumps(_runtime_structured, ensure_ascii=False, default=str))\n"
    return write_python_temp_script(workspace_id, safe_run_id, preamble + "\n" + code + postamble)


def _extract_structured_output(stdout: str) -> Any:
    structured = None
    for line in stdout.splitlines():
        if line.startswith("__LIANZHI_STRUCTURED__"):
            try:
                structured = json.loads(line[len("__LIANZHI_STRUCTURED__"):])
            except json.JSONDecodeError:
                return None
    return structured
