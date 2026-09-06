# core/tools/python_exec.py
"""Trusted-local best-effort Python execution.

This module is not a sandbox. ``execute_python_code`` selects one runner from
``core.tools.python_runner``; subprocess execution requires explicit trusted-local
opt-in, while exposed deployments use the isolated container runner.
"""

import ast
import json
import os
import shutil
import subprocess
import sys

from storage.workspace_files import write_python_temp_script

# ── Safe environment allowlist ──
# Only these environment variables are passed to the subprocess.
# Per P0-3: no API_KEY, TOKEN, SECRET, PASSWORD, proxy config.
_SAFE_ENV_ALLOWLIST = frozenset([
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "HOME",          # Needed for ~ expansion in some libraries
    "USER",
    "TMPDIR",
    "TEMP",
    "TMP",
])

# Blocklist patterns — match against UPPERCASE var names
_SENSITIVE_ENV_PATTERNS = [
    "API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MINIMAX_API_KEY",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "AWS_ACCESS_KEY", "AWS_SECRET", "AZURE_", "GCLOUD_",
    "CREDENTIAL", "PRIVATE_KEY", "SIGNING_KEY",
]


def _build_safe_env() -> dict[str, str]:
    """Build a minimal environment for python_exec subprocess.

    Only passes allowlisted vars. Blocks all sensitive patterns.
    """
    safe_env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper_key = key.upper()
        # Check against sensitive patterns
        blocked = False
        for pattern in _SENSITIVE_ENV_PATTERNS:
            if pattern in upper_key:
                blocked = True
                break
        if blocked:
            continue
        # Check allowlist (case-insensitive for path-like vars)
        if upper_key in _SAFE_ENV_ALLOWLIST or key in _SAFE_ENV_ALLOWLIST:
            safe_env[key] = value
    return safe_env


def _redact_stdout_stderr(stdout: str, stderr: str) -> tuple[str, str]:
    """Best-effort redaction of secrets that may appear in output."""
    import re as _re
    for pattern in _SENSITIVE_ENV_PATTERNS:
        # Redact common secret patterns like: KEY=value, "key": "value"
        stdout = _re.sub(
            rf'({pattern}\s*[=:]\s*)(\S+)',
            r'\1[REDACTED]',
            stdout,
            flags=_re.IGNORECASE,
        )
        stderr = _re.sub(
            rf'({pattern}\s*[=:]\s*)(\S+)',
            r'\1[REDACTED]',
            stderr,
            flags=_re.IGNORECASE,
        )
    return stdout, stderr


def validate_code(code: str) -> str:
    """Validate Python syntax without imposing a content policy."""
    ast.parse(code, mode="exec")
    return code


def execute_best_effort_python_code(
    code: str,
    workspace_id: str,
    run_id: str,
    timeout: int = 10,
    input_data=None,
) -> dict:
    """Run Python in an explicitly trusted local subprocess.

    This implementation is best effort only; it is not a sandbox.
    """
    import re
    if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", workspace_id):
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timeout_seconds": timeout,
            "error": "invalid_workspace_id",
        }

    # ── 2. Syntax check ──
    try:
        validate_code(code)
    except SyntaxError as e:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timeout_seconds": timeout,
            "error": f"Syntax check failed: {e}",
        }

    # ── 3. Validate and inject bounded structured input ──
    try:
        input_json = json.dumps(input_data if input_data is not None else {}, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        return {
            "ok": False, "exit_code": -1, "stdout": "", "stderr": "",
            "timeout_seconds": timeout, "error": f"input_data is not JSON serializable: {exc}",
        }
    # ── 4. Setup temp directory and script path ──
    safe_run_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(run_id) or "unknown") or "unknown"
    # Add a preamble that sanitizes the environment
    safe_preamble = (
        "# Auto-generated sandbox preamble — best-effort local sandbox, not container isolation\n"
        "# The selected runtime executes the model-provided code as written.\n"
        "import json as _runtime_json\n"
        f"input_data = _runtime_json.loads({input_json!r})\n"
    )
    safe_postamble = (
        "\ntry:\n"
        "    _runtime_structured = result\n"
        "except NameError:\n"
        "    _runtime_structured = None\n"
        "if _runtime_structured is not None:\n"
        "    print('__LIANZHI_STRUCTURED__' + _runtime_json.dumps(_runtime_structured, ensure_ascii=False, default=str))\n"
    )
    temp_dir, script_path = write_python_temp_script(
        workspace_id,
        safe_run_id,
        safe_preamble + "\n" + code + safe_postamble,
    )

    # ── 5. Execute in subprocess with minimal environment ──
    try:
        safe_env = _build_safe_env()
        result = subprocess.run(
            [sys.executable, str(script_path)],
            timeout=timeout,
            cwd=str(temp_dir),
            capture_output=True,
            text=True,
            env=safe_env,  # P0-3: isolated env — no API keys, tokens, or proxy config
        )
        stdout, stderr_out = _redact_stdout_stderr(
            (result.stdout or ""),
            (result.stderr or ""),
        )
        structured_output = None
        visible_lines = []
        for line in stdout.splitlines():
            if line.startswith("__LIANZHI_STRUCTURED__"):
                try:
                    structured_output = json.loads(line[len("__LIANZHI_STRUCTURED__"):])
                except json.JSONDecodeError:
                    stderr_out = (stderr_out + "\nstructured result serialization failed").strip()
                continue
            visible_lines.append(line)
        stdout = "\n".join(visible_lines)
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr_out,
            "structured_output": structured_output,
            "timeout_seconds": timeout,
            "error": "" if result.returncode == 0 else (stderr_out or f"Python exited with code {result.returncode}"),
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timeout_seconds": timeout,
            "error": f"Execution timed out after {timeout}s",
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timeout_seconds": timeout,
            "error": "Python interpreter not found",
        }
    except Exception as e:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timeout_seconds": timeout,
            "error": str(e)[:200],
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def execute_python_code(code: str, workspace_id: str, run_id: str, timeout: int = 10, input_data=None) -> dict:
    """Run Python through the single policy-selected execution runner.

    Best-effort local subprocess execution is available only after an explicit
    trusted-local opt-in. Network-exposed or multi-user deployments select the
    Docker strong-isolation runner and fail closed if it is unavailable.
    """
    from core.tools.python_runner import select_python_runner

    return select_python_runner().execute(
        code=code,
        workspace_id=workspace_id,
        run_id=run_id,
        timeout=timeout,
        input_data=input_data,
    )
