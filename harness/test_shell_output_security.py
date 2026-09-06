"""Shell output redaction and registry reload contracts.

This test file pins current production behavior:

  1. bash subprocesses receive only an allowlisted environment.
  2. subprocess stdout/stderr are redacted before returning to LLM context.
  3. registry reload invalidates the derived tool catalog snapshot.
"""

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_run_shell_blocks_api_keys_and_tokens():
    """``_build_safe_shell_env`` must NEVER inherit API_KEY / TOKEN /
    SECRET / PROXY / PASSWORD / CREDENTIAL / PRIVATE_KEY variants.

    We seed os.environ with a known sentinel value, call the helper,
    and assert the sentinel is absent from the result.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.tools.general_tools.command_tools import _build_safe_shell_env

    sentinels = {
        "OPENAI_API_KEY": "sk-sentinel-openai-1234567890abcdef",
        "LZCORE_ADMIN_TOKEN": "admin-sentinel-987654321",
        "LLM_TOKEN": "llm-sentinel-abcdef",
        "PROXY_URL": "http://user:pass@proxy.example.com",
        "GITHUB_PASSWORD": "pw-sentinel-abcdef",
        "MY_PRIVATE_KEY": "key-sentinel-abcdef",
    }
    sentinel_backup = {k: os.environ.get(k) for k in sentinels}
    try:
        for k, v in sentinels.items():
            os.environ[k] = v
        env = _build_safe_shell_env()
        for k in sentinels:
            assert k not in env, (
                f"_build_safe_shell_env leaked sensitive var {k!r}: "
                f"value={env.get(k)!r}"
            )
    finally:
        for k, old in sentinel_backup.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def test_run_shell_preserves_posix_basics():
    """Linux allowlist must keep PATH/HOME/USER/LANG/TZ/PWD so the
    bash subprocess can resolve executables and resolve locales.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.tools.general_tools.command_tools import _build_safe_shell_env

    sentinels = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/tester",
        "USER": "tester",
        "LANG": "en_US.UTF-8",
        "TZ": "UTC",
    }
    sentinel_backup = {k: os.environ.get(k) for k in sentinels}
    try:
        for k, v in sentinels.items():
            os.environ[k] = v
        env = _build_safe_shell_env()
        for k, v in sentinels.items():
            assert env.get(k) == v, (
                f"_build_safe_shell_env dropped POSIX-essential {k!r}; "
                f"expected {v!r}, got {env.get(k)!r}"
            )
    finally:
        for k, old in sentinel_backup.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def test_run_shell_redacts_subprocess_output():
    """_run_shell redacts the child process's stdout/stderr before
    returning — a command that intentionally ``echo $OPENAI_API_KEY``
    must NOT be echoed back to the LLM via the tool result.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))

    # Force-set a sentinel before invoking _run_shell so the child
    # shell can see it via $OPENAI_API_KEY.
    sentinels = {
        "OPENAI_API_KEY": "sk-runshellsentinel-1234567890",
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
    }
    sentinel_backup = {k: os.environ.get(k) for k in sentinels}
    try:
        for k, v in sentinels.items():
            os.environ[k] = v
        # We use the bash native form 'echo "$OPENAI_API_KEY"' to avoid
        # bash's plugin-style variable expansion masking.
        from core.tools.general_tools.shared import _run_shell
        result = _run_shell('echo "$OPENAI_API_KEY"')
        assert result["ok"], f"shell exec failed: {result}"
        assert "sk-runshellsentinel" not in result["stdout"], (
            f"_run_shell leaked api_key into stdout: {result['stdout']!r}"
        )
    finally:
        for k, old in sentinel_backup.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def test_run_shell_redacts_without_truncating_output():
    """Shell output is redacted but is not length-truncated for the model."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))

    from core.tools.general_tools import shared
    src = Path(shared.__file__).read_text(encoding="utf-8")
    stdout_redact_idx = src.index('stdout = redact_tool_output(stdout or "")')
    assert '[:_SHELL_MAX_OUTPUT]' not in src
    assert stdout_redact_idx >= 0


def test_exec_defaults_to_current_workspace(monkeypatch, tmp_path):
    from core.tools.schemas import ToolInvocation
    from core.tools.general_tools import command_tools

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    captured = {}

    def fake_run(
        command, cwd=None, shell="/bin/bash", env=None, timeout=None,
        cancel_check=None,
    ):
        captured.update(
            command=command,
            cwd=cwd,
            timeout=timeout,
            cancel_check=cancel_check,
        )
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(command_tools, "_run_shell", fake_run)
    result = command_tools.handle_command_exec(ToolInvocation(
        tool_id="exec.run",
        workspace_id="default",
        arguments={"action": "shell", "command": "pwd"},
    ))

    assert captured["cwd"] == str((tmp_path / "default").resolve())
    assert result["working_dir"] == "."


def test_exec_rejects_workdir_outside_current_workspace(monkeypatch, tmp_path):
    from core.tools.schemas import ToolInvocation
    from core.tools.general_tools import command_tools

    monkeypatch.setenv("LZCORE_WORKSPACE_ROOT", str(tmp_path))
    result = command_tools.handle_command_exec(ToolInvocation(
        tool_id="exec.run", workspace_id="default",
        arguments={"action": "shell", "command": "pwd", "working_dir": "/tmp"},
    ))
    assert result["ok"] is False
    assert "path_escape_denied" in result["error"]


def test_path_redaction_preserves_markdown_delimiters_and_spacing():
    from core.tools.redaction import redact_string
    from storage.redaction import redact_text

    core_text = redact_string("file: `/root/work/out.csv` next")
    assert core_text == "file: `[PATH_REDACTED]` next"

    stored_text = redact_text("file: `/home/user/work/out.csv` next")
    assert stored_text == "file: `[REDACTED_PATH]` next"


def test_tool_executor_redacts_non_dict_handler_output_before_returning():
    from core.tools.executor import ToolExecutor
    from core.tools.registry import ToolRegistry
    from core.tools.schemas import ToolInvocation, ToolSpec

    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(
            tool_id="test.text_output",
            name="test text output",
            description="test-only plain-text result",
            category="tool",
            risk_level="low",
            input_schema={"type": "object"},
            permission_action="read",
        ),
        lambda _invocation: "token=sk-test-secret-abcdefghijklmnopqrstuvwxyz",
    )

    result = ToolExecutor(registry).execute(ToolInvocation(
        tool_id="test.text_output", arguments={}, workspace_id="default",
    ))
    text = str(result.output.get("output") or "")
    assert "sk-test-secret" not in text
    assert "redacted" in text.lower()


def test_tool_executor_redacts_secret_in_handler_exception_before_returning():
    from core.tools.executor import ToolExecutor
    from core.tools.registry import ToolRegistry
    from core.tools.schemas import ToolInvocation, ToolSpec

    registry = ToolRegistry()
    registry.register_tool(
        ToolSpec(
            tool_id="test.exception_output",
            name="test exception output",
            description="test-only failing handler",
            category="tool",
            risk_level="low",
            input_schema={"type": "object"},
            permission_action="read",
        ),
        lambda _invocation: (_ for _ in ()).throw(
            RuntimeError("provider token=sk-test-secret-abcdefghijklmnopqrstuvwxyz")
        ),
    )

    result = ToolExecutor(registry).execute(ToolInvocation(
        tool_id="test.exception_output", arguments={}, workspace_id="default",
    ))
    error_text = " ".join(str(item) for item in result.errors)
    assert "sk-test-secret" not in error_text
    assert "redacted" in error_text.lower()


def test_streaming_tool_executor_redacts_secret_in_direct_runtime_exception():
    import asyncio

    from agent.llm.schemas import LLMToolCall
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.query_loop import StreamingToolExecutor

    class FailingRuntime:
        def invoke_raw(self, _tool_id, _arguments):
            raise RuntimeError("runtime token=sk-test-secret-abcdefghijklmnopqrstuvwxyz")

    result = asyncio.run(StreamingToolExecutor(
        FailingRuntime(), SSOTRuntimeConfig(),
    ).execute([LLMToolCall(id="call-1", name="web.manage", arguments={})]))[0]
    assert "sk-test-secret" not in str(result.error)
    assert "redacted" in str(result.error).lower()


def test_queryloop_redacts_tool_result_again_before_model_message():
    from agent.llm.schemas import LLMToolCall
    from core.runtime_engine.models import SSOTRuntimeConfig
    from core.runtime_engine.query_loop import QueryLoop, StreamingToolResult

    loop = QueryLoop(SSOTRuntimeConfig(), {}, None)
    messages = loop._append_tool_round(
        [],
        [LLMToolCall(id="call-1", name="web.manage", arguments={})],
        [StreamingToolResult(
            tool_name="web.manage", call_id="call-1", ok=True,
            output={"ok": True, "body": "token=sk-test-secret-abcdefghijklmnopqrstuvwxyz"},
        )],
    )
    content = str(messages[-1].content)
    assert "sk-test-secret" not in content
    assert "redacted" in content.lower()


def test_redactor_preserves_task_ids_while_masking_standalone_sk_keys():
    from core.tools.redaction import redact_string

    task_id = "task-718e272cfc06"
    secret = "sk-test-secret-abcdefghijklmnopqrstuvwxyz"

    assert redact_string(task_id) == task_id
    assert secret not in redact_string(f"Authorization: Bearer {secret}")
