from __future__ import annotations

import os
from core.tools.schemas import ToolInvocation
from storage.ids import validate_workspace_id

from core.tools.general_tools.shared import _caller_workspace, _error_inv, _ok, _result, _run_shell, _unavailable, _SHELL_MAX_OUTPUT
"""Split general tool handlers."""

# ── Environment variable keys blocked from user override ──
# Users must not replace PATH or inject library-loading variables
# that could redirect the subprocess to malicious code.
_BLOCKED_ENV_KEYS = {
    "PATH",
    "PYTHONPATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
}

# ── Sensitive env var name fragments (case-insensitive) ──
# Substrings that identify a variable as a credential / token / proxy
# and therefore must NEVER be inherited by a subprocess.
_SENSITIVE_PATTERNS = (
    "API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD",
    "PROXY", "CREDENTIAL", "PRIVATE_KEY", "SIGNING_KEY",
)

# ── Per-platform safe env allowlists ──
# Only vars in this set are passed through to the subprocess.
_PS_SAFE_ENV_ALLOWLIST = {
    "PATH", "HOME", "USER", "USERNAME", "COMPUTERNAME",
    "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
}

_LINUX_SAFE_ENV_ALLOWLIST = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM",
    "LANG", "LC_ALL", "LC_CTYPE", "LC_COLLATE", "LC_MESSAGES",
    "TZ", "TMPDIR", "PWD", "OLDPWD",
}


def _is_sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    return any(p in upper for p in _SENSITIVE_PATTERNS)


def _build_safe_env(allowlist: set[str] | None = None) -> dict:
    """Build a minimal subprocess environment.

    Shared by PowerShell and bash subprocess paths. Sensitive vars
    are always stripped; everything else is gated by the
    per-platform allowlist.
    """
    if allowlist is None:
        allowlist = _PS_SAFE_ENV_ALLOWLIST
    safe_env = {}
    for key, value in os.environ.items():
        # Always strip sensitive patterns regardless of platform.
        if _is_sensitive_env_key(key):
            continue
        # Allowlist check accepts both upper- and lower-case forms so
        # callers that pass {"PATH"} or {"path"} both work.
        if key in allowlist or key.upper() in allowlist:
            safe_env[key] = value
    return safe_env


def _build_safe_shell_env() -> dict:
    return _build_safe_env(
        _PS_SAFE_ENV_ALLOWLIST if os.name == "nt" else _LINUX_SAFE_ENV_ALLOWLIST
    )

def handle_command_approved_exec(inv: ToolInvocation) -> dict:
    """Run a local shell command through the native platform shell.

    Linux/macOS use ``/bin/bash -c``; Windows uses ``cmd.exe /d /s /c``.
    Safety limits: dangerous command detection, configurable timeout, output truncation.
    Base risk is medium; destructive patterns escalate to high-risk approval.
    """
    # Only accept `command`; alternate identifiers are never executed as shell.
    command = (inv.arguments.get("command") or "").strip()
    if not command:
        return _unavailable(inv, "command is required")

    # ── Safety: dangerous command detection ──
    from core.tools.general_tools.dangerous_commands import full_check
    safety = full_check(command)
    if safety["blocked"]:
        return {"ok": False, "error": safety["reason"],
                "warnings": safety["warnings"], "suspicious": safety["suspicious"]}

    # v3.7: pass through cwd, env_vars, timeout
    cwd = (inv.arguments.get("working_dir") or "").strip() or None
    env_vars = inv.arguments.get("env_vars")
    timeout = inv.arguments.get("timeout")
    if timeout is not None:
        timeout = int(timeout)

    # Sanitize user-provided env_vars: block dangerous overrides that
    # could replace the system PATH or inject malicious library paths.
    if isinstance(env_vars, dict):
        env_vars = {
            str(k): str(v) for k, v in env_vars.items()
            if str(k).upper() not in _BLOCKED_ENV_KEYS
            and not _is_sensitive_env_key(str(k))
        }

    result = _run_shell(command, cwd=cwd, env=env_vars, timeout=timeout)
    # Attach safety metadata + description to result
    if safety["warnings"]:
        result["warnings"] = safety["warnings"]
    if safety["suspicious"]:
        result["suspicious"] = safety["suspicious"]
    description = (inv.arguments.get("description") or "").strip()
    if description:
        result["description"] = description
    return _result(inv, result.pop("ok", False), result)

def handle_powershell_approved_script(inv: ToolInvocation) -> dict:
    """PowerShell script execution on Windows.

    Accepts a PowerShell command string, executes via powershell -Command.
    Safety limits: dangerous command detection, 15s timeout, output truncation.
    Base risk is medium; destructive patterns escalate to high-risk approval.

    Security: subprocess uses a minimal safe environment (mirrors
    python_exec's P0-3 model) — no API keys, tokens, or proxy config.
    """
    import platform
    if platform.system() != "Windows":
        return _unavailable(inv, "PowerShell execution only available on Windows. Use exec.run on Linux/macOS.")
    command = (inv.arguments.get("command") or "").strip()
    if not command:
        return _unavailable(inv, "command is required")

    # ── Safety: dangerous command detection ──
    from core.tools.general_tools.dangerous_commands import full_check
    safety = full_check(command)
    if safety["blocked"]:
        return {"ok": False, "error": safety["reason"],
                "warnings": safety["warnings"], "suspicious": safety["suspicious"]}
    import shutil
    import subprocess
    try:
        safe_env = _build_safe_env()
        env_vars = inv.arguments.get("env_vars")
        if isinstance(env_vars, dict):
            safe_env.update({
                str(key): str(value)
                for key, value in env_vars.items()
                if str(key).upper() not in _BLOCKED_ENV_KEYS
                and not _is_sensitive_env_key(str(key))
            })
        timeout = max(1, min(int(inv.arguments.get("timeout", 120) or 120), 600))
        cwd = str(inv.arguments.get("working_dir") or "").strip() or None
        executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not executable:
            return _error_inv(inv, "PowerShell executable not found")
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, errors="replace", timeout=timeout,
            env=safe_env,
            cwd=cwd,
        )
        stdout = (result.stdout or "")[:_SHELL_MAX_OUTPUT]
        stderr = (result.stderr or "")[:_SHELL_MAX_OUTPUT]
        output = {
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        if safety["warnings"]:
            output["warnings"] = safety["warnings"]
        if safety["suspicious"]:
            output["suspicious"] = safety["suspicious"]
        if result.returncode != 0:
            output["error"] = stderr.strip() or f"PowerShell exited with code {result.returncode}"
        return _result(inv, result.returncode == 0, output)
    except subprocess.TimeoutExpired:
        return _error_inv(inv, f"command timed out after {timeout}s")
    except FileNotFoundError:
        return _error_inv(inv, "powershell not found")
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_slash_run(inv: ToolInvocation) -> dict:
    """Execute a slash command via the command system."""
    args = inv.arguments
    command = str(args.get("command", "")).strip()
    cmd_args = str(args.get("args", "")).strip()

    if not command:
        return _error_inv(inv, "command is required")

    try:
        from agent.runtime.command_system import execute_command
        result = execute_command(command, cmd_args, getattr(inv, 'session_id', None), getattr(inv, 'workspace_id', None))
        return _ok(inv, f"Slash command '{command}' executed.", {"command": command, "result": result})
    except ImportError:
        return _error_inv(inv, "command system not available")
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

def handle_python_exec(inv: ToolInvocation) -> dict:
    """Execute Python code in an AST-checked sandbox.

    High risk tool. Code is parsed with AST to reject forbidden imports,
    builtins, and dunder access before execution. Runs in a subprocess with
    timeout. Requires explicit user approval.
    """
    workspace_id = _caller_workspace(inv)
    run_id = inv.arguments.get("run_id", "")
    code = str(inv.arguments.get("code", "")).strip()
    timeout = min(int(inv.arguments.get("timeout", 30) or 30), 60)  # v3.7: max 60s

    if not code:
        return _error_inv(inv, "code is required")

    try:
        validate_workspace_id(workspace_id)
        from core.tools.python_exec import execute_python_code
        result = execute_python_code(
            code=code,
            workspace_id=workspace_id,
            run_id=run_id,
            timeout=timeout,
        )
        description = (inv.arguments.get("description") or "").strip()
        if description:
            result["description"] = description
        return _result(inv, result.pop("ok", False), result)
    except Exception as e:
        return _error_inv(inv, str(e)[:200])

__all__ = ['handle_command_approved_exec', 'handle_powershell_approved_script', 'handle_slash_run', 'handle_python_exec']
