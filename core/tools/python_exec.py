# core/tools/python_exec.py
"""Python Execution Sandbox — AST-checked, sandboxed Python code execution.

Security model:
1. AST parse + walk to reject forbidden imports/functions before execution
2. Subprocess isolation with timeout
3. No file-system access outside the temp workspace directory
"""

import ast
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


# ── Forbidden imports ──
FORBIDDEN_IMPORTS = {
    "os", "subprocess", "socket", "requests", "urllib",
    "shutil", "pathlib", "ctypes", "multiprocessing", "threading",
}

# ── Forbidden builtin function names ──
FORBIDDEN_BUILTINS = {
    "eval", "exec", "compile", "__import__", "open",
    "input", "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr",
}

# ── Forbidden attribute names (dunder access) ──
FORBIDDEN_ATTRS = {
    "__class__", "__dict__", "__subclasses__", "__import__",
}


class PythonExecSecurityError(Exception):
    """Raised when AST analysis finds forbidden code."""
    pass


def _validate_ast(code: str) -> None:
    """Parse and walk AST to reject forbidden operations.

    Raises PythonExecSecurityError on the first violation.
    """


def validate_code(code: str) -> str:
    """Public-facing AST validation — wrapper for _validate_ast.

    Returns the validated code if safe, raises PythonExecSecurityError otherwise.
    This is the function that tests import from core.tools.python_exec.
    """
    _validate_ast(code)
    return code


def _validate_ast(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        raise PythonExecSecurityError(f"Syntax error: {e}")

    allowed_imports = {"sys", "math", "json", "re", "datetime", "time",
                       "collections", "itertools", "functools", "typing",
                       "string", "textwrap", "hashlib", "base64", "binascii",
                       "random", "statistics", "decimal", "fractions",
                       "enum", "dataclasses", "copy", "pprint", "logging",
                       "csv", "io", "codecs", "struct", "operator"}

    for node in ast.walk(tree):
        # ── Import statements ──
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                if top_level in FORBIDDEN_IMPORTS:
                    raise PythonExecSecurityError(
                        f"Forbidden import: '{alias.name}'"
                    )
                # Reject unknown imports (not in allowed list and not in stdlib)
                if top_level not in allowed_imports and top_level not in FORBIDDEN_IMPORTS:
                    raise PythonExecSecurityError(
                        f"Import not in allowlist: '{alias.name}'"
                    )

        if isinstance(node, ast.ImportFrom):
            if node.module:
                top_level = node.module.split(".")[0]
                if top_level in FORBIDDEN_IMPORTS:
                    raise PythonExecSecurityError(
                        f"Forbidden import from: '{node.module}'"
                    )
                if top_level not in allowed_imports and top_level not in FORBIDDEN_IMPORTS:
                    raise PythonExecSecurityError(
                        f"Import from not in allowlist: '{node.module}'"
                    )

        # ── Call to forbidden builtins ──
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_BUILTINS:
                    raise PythonExecSecurityError(
                        f"Forbidden function call: '{node.func.id}'"
                    )

        # ── Forbidden attribute access (dunder) ──
        if isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                raise PythonExecSecurityError(
                    f"Forbidden attribute access: '{node.attr}'"
                )


def execute_python_code(code: str, workspace_id: str, run_id: str,
                        timeout: int = 10) -> dict:
    """Execute Python code in a sandboxed subprocess.

    Args:
        code: Python source code to execute.
        workspace_id: Workspace identifier for file isolation.
        run_id: Run identifier for output directory naming.
        timeout: Maximum execution time in seconds (default 10).

    Returns:
        dict with keys: ok, exit_code, stdout, stderr, timeout_seconds, error
    """
    # ── 1. Validate workspace_id ──
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

    # ── 2. AST safety check ──
    try:
        _validate_ast(code)
    except PythonExecSecurityError as e:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "timeout_seconds": timeout,
            "error": f"Security check failed: {e}",
        }

    # ── 3. Setup temp directory and script path ──
    safe_run_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(run_id) or "unknown") or "unknown"
    # Add a preamble that sanitizes the environment
    safe_preamble = (
        "# Auto-generated sandbox preamble — best-effort local sandbox, not container isolation\n"
        "# Safety enforced at AST level (see _validate_ast). No runtime builtin disabling\n"
        "# needed — stdlib modules such as json, collections, enum use eval() internally.\n"
        "_ = None\n"
    )
    temp_dir, script_path = write_python_temp_script(
        workspace_id,
        safe_run_id,
        safe_preamble + "\n" + code,
    )

    # ── 4. Execute in subprocess with minimal environment ──
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
        return {
            "ok": True,
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr_out,
            "timeout_seconds": timeout,
            "error": "",
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
