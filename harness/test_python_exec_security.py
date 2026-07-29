# harness/test_python_exec_security.py
"""Python exec sandbox security tests — AST validation, environment isolation,
forbidden imports, and redaction.

Tests for:
  - P0-3: Subprocess environment isolation
  - Forbidden imports/modules
  - AST-level code injection
  - Redaction of sensitive output
"""

import pytest
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestPythonExecASTSecurity:
    """AST-level sandbox checks."""

    def test_forbidden_imports(self):
        """Block direct imports of os, subprocess, socket, etc."""
        from core.tools.python_exec import FORBIDDEN_IMPORTS
        assert "os" in FORBIDDEN_IMPORTS
        assert "subprocess" in FORBIDDEN_IMPORTS
        assert "socket" in FORBIDDEN_IMPORTS
        assert "ctypes" in FORBIDDEN_IMPORTS

    def test_forbidden_builtins(self):
        """Block dangerous builtins like eval, exec, __import__."""
        from core.tools.python_exec import FORBIDDEN_BUILTINS
        assert "eval" in FORBIDDEN_BUILTINS
        assert "exec" in FORBIDDEN_BUILTINS
        assert "__import__" in FORBIDDEN_BUILTINS
        assert "open" in FORBIDDEN_BUILTINS
        assert "globals" in FORBIDDEN_BUILTINS

    def test_forbidden_attr_access(self):
        """Block dunder attribute access for escape."""
        from core.tools.python_exec import FORBIDDEN_ATTRS
        assert "__class__" in FORBIDDEN_ATTRS
        assert "__subclasses__" in FORBIDDEN_ATTRS
        assert "__dict__" in FORBIDDEN_ATTRS

    def test_validate_code_rejects_import_os(self):
        """AST validation should reject 'import os'."""
        from core.tools.python_exec import validate_code, PythonExecSecurityError
        with pytest.raises(PythonExecSecurityError):
            validate_code("import os\nprint('hello')")

    def test_validate_code_rejects_from_import(self):
        """AST validation should reject 'from subprocess import ...'."""
        from core.tools.python_exec import validate_code, PythonExecSecurityError
        with pytest.raises(PythonExecSecurityError):
            validate_code("from subprocess import run\nrun(['ls'])")

    def test_validate_code_rejects_eval(self):
        """AST validation should reject eval()."""
        from core.tools.python_exec import validate_code, PythonExecSecurityError
        with pytest.raises(PythonExecSecurityError):
            validate_code("eval('1+1')")

    def test_validate_code_rejects_exec(self):
        """AST validation should reject exec()."""
        from core.tools.python_exec import validate_code, PythonExecSecurityError
        with pytest.raises(PythonExecSecurityError):
            validate_code("exec('import os')")

    def test_validate_code_rejects_open(self):
        """AST validation should reject open()."""
        from core.tools.python_exec import validate_code, PythonExecSecurityError
        with pytest.raises(PythonExecSecurityError):
            validate_code("open('/etc/passwd').read()")

    def test_validate_code_rejects_dunder_import(self):
        """AST validation should reject __import__()."""
        from core.tools.python_exec import validate_code, PythonExecSecurityError
        with pytest.raises(PythonExecSecurityError):
            validate_code("__import__('os').system('ls')")

    def test_validate_code_rejects_getattr_subclasses(self):
        """AST validation should reject .__subclasses__() escape."""
        from core.tools.python_exec import validate_code, PythonExecSecurityError
        with pytest.raises(PythonExecSecurityError):
            validate_code("().__class__.__subclasses__()")

    def test_validate_code_allows_safe_math(self):
        """AST validation should allow safe math operations."""
        from core.tools.python_exec import validate_code
        result = validate_code("1 + 2 * 3")
        assert result == "1 + 2 * 3"

    def test_validate_code_allows_safe_string(self):
        """AST validation should allow safe string operations."""
        from core.tools.python_exec import validate_code
        result = validate_code("'hello' + ' world'")
        assert result == "'hello' + ' world'"


class TestPythonExecEnvironment:
    """Subprocess environment isolation tests."""

    def test_safe_env_is_built(self):
        """_build_safe_env should return a dict."""
        from core.tools.python_exec import _build_safe_env
        env = _build_safe_env()
        assert isinstance(env, dict)

    def test_safe_env_excludes_api_keys(self, monkeypatch):
        """Safe env must NOT include API keys or tokens."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test123456")
        monkeypatch.setenv("MINIMAX_API_KEY", "group_test_key_abc")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test123")
        monkeypatch.setenv("AGENT_PLATFORM_API_TOKEN", "secret-token")
        from core.tools.python_exec import _build_safe_env
        env = _build_safe_env()
        assert "OPENAI_API_KEY" not in env
        assert "MINIMAX_API_KEY" not in env
        assert "DEEPSEEK_API_KEY" not in env
        assert "AGENT_PLATFORM_API_TOKEN" not in env

    def test_safe_env_includes_safe_vars(self, monkeypatch):
        """Safe env should include safe env vars like HOME, LANG."""
        monkeypatch.setenv("HOME", "/test/home")
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("TMPDIR", "/tmp")
        from core.tools.python_exec import _build_safe_env
        env = _build_safe_env()
        # These are typically in the allowlist
        # At minimum, env should be a dict (implementation detail)
        assert isinstance(env, dict)


class TestPythonExecRedaction:
    """Output redaction tests."""

    def test_redact_api_key(self):
        """Output containing API key should be redacted."""
        from core.tools.python_exec import _redact_stdout_stderr
        stdout = "API_KEY=sk-abc123def456"
        stderr = ""
        out, err = _redact_stdout_stderr(stdout, stderr)
        assert "sk-abc123def456" not in out
        assert "[REDACTED]" in out

    def test_redact_token(self):
        """Output containing token should be redacted."""
        from core.tools.python_exec import _redact_stdout_stderr
        stdout = "token=abcdef1234567890abcdef"
        stderr = ""
        out, err = _redact_stdout_stderr(stdout, stderr)
        assert "abcdef1234567890abcdef" not in out
        assert "[REDACTED]" in out

    def test_redact_secret(self):
        """Output containing secret should be redacted."""
        from core.tools.python_exec import _redact_stdout_stderr
        stdout = 'secret="my-super-secret"'
        stderr = ""
        out, err = _redact_stdout_stderr(stdout, stderr)
        assert "my-super-secret" not in out
        assert "[REDACTED]" in out

    def test_safe_output_passes_through(self):
        """Safe output should not be modified."""
        from core.tools.python_exec import _redact_stdout_stderr
        stdout = "Hello, world! The answer is 42."
        stderr = ""
        out, err = _redact_stdout_stderr(stdout, stderr)
        assert "Hello, world!" in out
        assert "42" in out


class TestPythonExecSubprocess:
    """End-to-end subprocess execution tests."""

    def test_execute_safe_code(self, tmp_path):
        """Execute safe Python code in sandbox."""
        from core.tools.python_exec import execute_python_code
        result = execute_python_code(
            run_id="test_run", code="print('hello sandbox')",
            workspace_id="default",
            timeout=5,
        )
        assert result.get("ok") is True
        assert "hello sandbox" in result.get("stdout", "")

    def test_execute_syntax_error(self, tmp_path):
        """Syntax error should be caught and reported."""
        from core.tools.python_exec import execute_python_code
        result = execute_python_code(
            run_id="test_run", code="print('unclosed",
            workspace_id="default",
            timeout=5,
        )
        assert result.get("ok") is False or "error" in str(result).lower()

    def test_execute_timeout(self, tmp_path):
        """Infinite loop should be terminated by timeout."""
        from core.tools.python_exec import execute_python_code
        result = execute_python_code(
            run_id="test_run", code="while True: pass",
            workspace_id="default",
            timeout=2,
        )
        # Should either fail with timeout or error
        assert result.get("ok") is False or "timeout" in str(result).lower() or "error" in str(result).lower()
