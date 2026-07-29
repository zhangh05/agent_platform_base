# core/tools/general_tools/shared.py
"""Shared helpers for split general tools."""

import json
import os
import re
import time
from functools import wraps
from pathlib import Path
from typing import Any, Optional

from core.tools.schemas import ToolSpec, ToolInvocation, ToolResult
from core.tools.redaction import redact_tool_output
from core.tools.path_security import PathSecurityError, safe_workspace_path
from storage.ids import validate_workspace_id

ROOT = Path(__file__).resolve().parents[3]

_SHELL_TIMEOUT = 30
_SHELL_MAX_OUTPUT = 50000


# ═══════════════ Helpers ═══════════════
def _workspace_path(workspace_id: str, subpath: str = "") -> Path:
    try:
        return safe_workspace_path(workspace_id, subpath)
    except PathSecurityError as exc:
        raise ValueError(str(exc)) from exc
# (safe implementation with proper traversal + symlink + encoding checks)


def _caller_workspace(inv: "ToolInvocation") -> str:
    """Return the runtime-validated workspace for a tool invocation.

    The runtime context is authoritative. If the model also supplied a
    workspace_id argument it must match the runtime workspace.
    """
    requested = str((getattr(inv, "arguments", {}) or {}).get("workspace_id") or "").strip()
    caller = str(getattr(inv, "workspace_id", "") or "").strip()
    if caller and requested and caller != requested:
        raise ValueError(f"workspace_id mismatch: caller={caller!r}, requested={requested!r}")
    workspace_id = caller or requested
    if not workspace_id:
        raise ValueError("workspace_id is required")
    validate_workspace_id(workspace_id)
    return workspace_id


def _safe_preview(text: str, max_chars: int = 500) -> str:
    """Truncate text to safe preview length."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"...[truncated, {len(text)} chars total]"


def _contract(inv: "ToolInvocation", ok: bool, status: str, summary: str,
              output: dict = None) -> dict:
    """Build a full LLM-facing tool result contract.

    Every tool handler in v3.0 returns a dict that carries the public
    tool_id, a status, and a short human-readable summary. The raw
    tool-specific payload is preserved under its own keys.
    """
    out: dict[str, Any] = {"ok": ok, "status": status}
    if summary:
        out["summary"] = summary
    elif "summary" not in (output or {}):
        out["summary"] = _summarize(output or {})
    else:
        out["summary"] = (output or {}).get("summary") or _summarize(output or {})
    tool_id = getattr(inv, "tool_id", "")
    if tool_id:
        out["tool_id"] = tool_id
    if output:
        for k, v in output.items():
            out.setdefault(k, v)
    return out


def _ok(inv: "ToolInvocation", summary: str = "", output: dict = None) -> dict:
    return _contract(inv, True, "ok", summary, output)


def _error(msg: str) -> dict:
    return {"ok": False, "status": "failed", "summary": msg, "error": msg, "errors": [msg]}


def _error_inv(inv: "ToolInvocation", msg: str) -> dict:
    """Like _error but attaches the tool_id so the LLM-facing contract
    is consistent across success and failure paths."""
    out = _error(msg)
    tool_id = getattr(inv, "tool_id", "")
    if tool_id:
        out["tool_id"] = tool_id
    return out


def _unavailable(inv: "ToolInvocation", msg: str) -> dict:
    """Return a tool-id-aware error result for a tool that is not usable
    in the current environment (e.g. PowerShell on Linux). Carries the
    full LLM-facing contract (tool_id, status, summary) so callers can
    render a useful message instead of an empty failure."""
    return {
        "ok": False,
        "tool_id": getattr(inv, "tool_id", ""),
        "status": "unavailable",
        "summary": msg,
        "error": msg,
        "errors": [msg],
    }


def _result(inv: "ToolInvocation", ok: bool, output: dict = None) -> dict:
    """Build a tool result dict with the full LLM-facing contract."""
    if not output:
        output = {}
    summary = output.get("summary") or _summarize(output)
    status = "ok" if ok else "failed"
    out = _contract(inv, ok, status, summary, output)
    if not ok and "errors" not in out:
        msg = out.get("error") or summary or "failed"
        out["errors"] = [msg]
    return out


def _summarize(output: dict) -> str:
    """Best-effort short summary derived from a handler's output dict.

    v2.3.3: Extended to cover common tool output keys so the LLM-facing
    summary is informative instead of always returning "Completed."
    """
    for k in ("summary", "title", "message", "stdout", "stderr", "text", "error"):
        v = output.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:200]
    if "count" in output and "files" in output:
        return f"Listed {output['count']} file(s)."
    if "count" in output and "results" in output:
        return f"Found {output['count']} result(s)."
    if "count" in output and "chunks" in output:
        return f"Found {output['count']} chunk(s)."
    if "count" in output:
        return f"Returned {output['count']} item(s)."
    # Common structured output keys — give the LLM a meaningful hint
    if "markdown" in output:
        md = str(output["markdown"])
        return f"Markdown rendered ({len(md)} chars)."
    if "document" in output:
        doc = str(output["document"])
        return f"Document rendered ({len(doc)} chars)."
    if "table" in output:
        tbl = str(output["table"])
        return f"Table rendered ({len(tbl)} chars)." if tbl else "Table rendered (empty)."
    if "mermaid" in output:
        return "Mermaid diagram rendered."
    if "redacted" in output:
        return f"Text redacted ({output.get('original_length', 0)} → {len(str(output['redacted']))} chars)."
    if "keywords" in output:
        kws = output["keywords"]
        return f"Extracted {len(kws)} keyword(s)." if isinstance(kws, (list, tuple)) else "Keywords extracted."
    if "classification" in output:
        return f"Classified as '{output['classification']}'."
    if "diff" in output:
        return f"Diff computed ({output.get('changed_lines', '?')} lines changed)."
    if "extracted" in output:
        ex = output["extracted"]
        return f"Extracted {len(ex)} row(s)" if isinstance(ex, (list, tuple)) else "Data extracted."
    if "valid" in output:
        return f"Validated ({output.get('type', 'unknown')}): {'valid' if output['valid'] else 'invalid'}."
    if "filepath" in output:
        return f"Written to {output['filepath']} ({output.get('size', '?')} bytes)."
    if "workspace_id" in output:
        return f"Workspace metadata: {'exists' if output.get('exists') else 'not found'}."
    if "archive_count" in output:
        return f"Archive preview: {output['archive_count']} item(s)."
    if "candidate_count" in output:
        return f"Retention preview: {output['candidate_count']} candidate(s)."
    return "Tool completed without structured output."


def _generate_diff_preview(old: str, new: str, max_lines: int = 6) -> str:
    """Generate a compact diff preview for file edit operations."""
    old_lines = old.strip().split("\n")
    new_lines = new.strip().split("\n")
    preview = []
    # Show first few lines of old and new side by side
    for i in range(min(len(old_lines), len(new_lines), max_lines)):
        if old_lines[i] != new_lines[i]:
            preview.append(f"- {old_lines[i]}")
            preview.append(f"+ {new_lines[i]}")
        else:
            preview.append(f"  {old_lines[i]}")
    return "\n".join(preview)


# ═══════════════ A. Artifact Tools ═══════════════









def _persist_artifact_tags(ws: str, art_id: str, tags: list) -> None:
    """Best-effort: persist artifact tags through the artifact store."""
    try:
        from artifacts.store import update_artifact_tags
        update_artifact_tags(ws, art_id, tags)
    except Exception:
        pass




# ═══════════════ B. Knowledge Tools ═══════════════













# ═══════════════ C. Web Tools ═══════════════

_PRIVATE_IP_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                         "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                         "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                         "172.30.", "172.31.", "192.168.", "127.", "0.", "169.254.")



# Re-export shared web handlers from the split implementation module.
from core.tools.general_tools.shared_web import *

def _shell_argv(command, shell: str = "/bin/bash", os_name: str | None = None):
    """Build the native shell argv without invoking a process."""
    import os as _os
    if isinstance(command, list):
        return command
    if (os_name or _os.name) == "nt":
        command_shell = _os.environ.get("COMSPEC") or "cmd.exe"
        return [command_shell, "/d", "/s", "/c", command]
    return [shell, "-c", command]


def _run_shell(command: str, cwd: str = None, shell: str = "/bin/bash",
               env: dict = None, timeout: int = None) -> dict:
    """Execute a shell command with safety limits + process tree cleanup.

    Uses process group isolation (os.setsid on Unix, CREATE_NEW_PROCESS_GROUP
    on Windows) so that on timeout ALL child processes are killed — not just
    the immediate parent.
    """
    import subprocess, os as _os
    if not command or not command.strip():
        return {"ok": False, "error": "empty command"}

    # Build safe subprocess environment using the per-platform allowlist.
    from core.tools.general_tools.command_tools import _build_safe_shell_env
    sub_env = _build_safe_shell_env()
    # Prepend sys.executable dir to PATH so python3 is always found
    # (required by exec.run-based analysis tools).
    import sys as _sys
    _python_bin = str(_os.path.dirname(_sys.executable))
    existing_path = sub_env.get("PATH", "")
    if _python_bin and _python_bin not in existing_path.split(_os.pathsep):
        sub_env["PATH"] = _python_bin + _os.pathsep + existing_path
    # Apply caller-provided overrides on top (caller must not bypass
    # the allowlist for sensitive keys — but caller sanitization at
    # handle_command_approved_exec already strips *_BLOCKED_ENV_KEYS).
    if env:
        sub_env.update(env)

    actual_timeout = timeout or _SHELL_TIMEOUT

    # ── Process group isolation ──
    popen_kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "errors": "replace",
        "cwd": cwd or str(ROOT),
        "env": sub_env,
    }
    if _os.name == "nt":
        # Windows: CREATE_NEW_PROCESS_GROUP for tree kill via taskkill /T
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Linux/macOS: setsid creates a new session/process group
        popen_kwargs["preexec_fn"] = _os.setsid

    proc = None
    try:
        # The previous unconditional /bin/bash default made exec.run unusable
        # on Windows; choose the native shell at this final execution boundary.
        argv = _shell_argv(command, shell=shell, os_name=_os.name)
        proc = subprocess.Popen(argv, **popen_kwargs)
        stdout, stderr = proc.communicate(timeout=actual_timeout)

        from core.tools.redaction import redact_tool_output
        stdout = redact_tool_output(stdout or "")[:_SHELL_MAX_OUTPUT]
        stderr = redact_tool_output(stderr or "")[:_SHELL_MAX_OUTPUT]
        ok = proc.returncode == 0
        result = {
            "ok": ok,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timeout_seconds": actual_timeout,
        }
        if not ok:
            result["error"] = stderr.strip() or f"Command exited with code {proc.returncode}"
        return result
    except subprocess.TimeoutExpired:
        # ── Kill the entire process group, not just the parent ──
        from core.tools.general_tools.process_manager import kill_process_tree
        try:
            kill_process_tree(proc.pid)
        except Exception:
            pass
        # Collect whatever output was produced before the kill
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
        return {"ok": False, "error": f"command timed out after {actual_timeout}s",
                "process_tree_killed": True}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"command not found: {e}"}
    except Exception as e:
        # Attempt cleanup on any error
        if proc is not None:
            try:
                if proc.poll() is None:
                    from core.tools.general_tools.process_manager import kill_process_tree
                    kill_process_tree(proc.pid)
            except Exception:
                pass
        return {"ok": False, "error": str(e)[:200]}






# ═══════════════ J. Python Exec Tool (high risk, AST-sandboxed) ═══════════════



# ═══════════════ K. Session Snapshot / Rewind Tools ═══════════════











# ═══════════════ L. Agent Spawn (Sub-Agent) Tool ═══════════════

__all__ = [name for name in globals() if not name.startswith("__")]
