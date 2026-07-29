# core/tools/dangerous_patterns.py
"""Single source of truth for "destructive command" detection.

A destructive command is one that, when executed, can permanently or
significantly damage the local host or remote device state. Examples:
``rm -rf /tmp/foo``, ``dd if=/dev/zero of=/dev/sda``, ``mkfs``, fork
bombs, etc. These are the only command-level signals that should
escalate a tool call to ``high`` risk + ``requires_approval`` (i.e. the
approval bubble UX).

The set of patterns lives here so policy and risk evaluation agree on
what "destructive" means.

Note: this is a *command* check, not a *file path* check. Sensitive
Workspace file paths are validated by ``core.tools.path_security``.
"""

from __future__ import annotations

import re
from typing import Optional


# ── Destructive shell / PowerShell command patterns ──────────────────────
# All patterns are case-insensitive. Each pattern is matched against
# individual argument *string* values (not the full argument dict),
# so user text that happens to contain "rm -rf" in a non-command
# field is not falsely flagged.

_DANGEROUS_PATTERNS: tuple[re.Pattern, ...] = (
    # Unix shell: destructive delete
    re.compile(r"\brm\s+-(r|f|rf|fr)\b", re.I),
    re.compile(r"\brm\s+-(r|f|rf|fr)\s", re.I),
    re.compile(r"\brm\s+--recursive\b", re.I),
    re.compile(r"\brm\s+--force\b", re.I),
    re.compile(r"\brm\s+-[a-z]*[rf][a-z]*\b", re.I),
    # Windows shell: destructive delete
    re.compile(r"\bdel\s+/s\b", re.I),
    re.compile(r"\bdel\s+/q\b", re.I),
    re.compile(r"\bRemove-Item\b.*-(Recurse|Force)\b", re.I),
    re.compile(r"\bformat\s+[A-Za-z]:", re.I),
    # Disk / filesystem destruction
    re.compile(r"\bmkfs\b", re.I),
    re.compile(r"\bfdisk\b", re.I),
    re.compile(r"\bparted\b", re.I),
    re.compile(r"\bdd\s+if=", re.I),
    re.compile(r">\s*/dev/sd[a-z]", re.I),
    re.compile(r">\s*/dev/hd[a-z]", re.I),
    re.compile(r">\s*/dev/nvme", re.I),
    # Privilege / permission bombs
    re.compile(r"\bchmod\s+777\b", re.I),
    re.compile(r"\bchmod\s+-R\s+777\b", re.I),
    # System control
    re.compile(r"\bshutdown\b", re.I),
    re.compile(r"\breboot\b", re.I),
    re.compile(r"\bhalt\b", re.I),
    re.compile(r"\binit\s+[06]\b", re.I),
    re.compile(r"\bpoweroff\b", re.I),
    # Firewall flush
    re.compile(r"\biptables\s+-F\b", re.I),
    re.compile(r"\bufw\s+disable\b", re.I),
    # Download-then-execute chains (always destructive, regardless of pipe)
    re.compile(r"\bcurl\b.*\|\s*sh\b", re.I),
    re.compile(r"\bcurl\b.*\|\s*bash\b", re.I),
    re.compile(r"\bwget\b.*\|\s*sh\b", re.I),
    re.compile(r"\bwget\b.*\|\s*bash\b", re.I),
    re.compile(r"\bwget\b.*-O\s*-", re.I),
    # PowerShell remote-execution / encoded
    re.compile(r"\bInvoke-Expression\b", re.I),
    re.compile(r"\biex\b", re.I),
    re.compile(r"\bInvoke-WebRequest\b.*\|\s*(iex|IEX)\b", re.I),
    re.compile(r"\bDownloadString\b", re.I),
    re.compile(r"\bStart-Process\b", re.I),
    re.compile(r"\bSet-ExecutionPolicy\b", re.I),
    # Fork bomb
    re.compile(r":\s*\(\s*\)\s*\{", re.I),
    re.compile(r"\bchown\s+-R\s+.*\s+/", re.I),
    # eval / exec in shell context (could be benign but high risk for
    # arbitrary code execution; treat as high)
    re.compile(r"^\s*eval\s+", re.I),
    re.compile(r"^\s*exec\s+", re.I),
)


# Fields in the argument dict that should be scanned for dangerous
# command content. We do NOT scan every string (that would catch user
# text that mentions "rm -rf" without being a command).
_COMMAND_FIELD_NAMES = {
    "command", "cmd", "shell", "shell_command", "args",
    "script", "script_body", "exec",
}


def scan_arguments_for_dangerous(arguments: dict) -> Optional[str]:
    """Return the matched pattern string if any argument value contains
    a destructive command pattern, else None.

    Only fields whose key is a known command-bearing field are
    scanned, to avoid false positives on user-provided free text.
    """
    if not arguments:
        return None
    for key, value in arguments.items():
        if not isinstance(value, str):
            continue
        key_l = str(key).lower()
        # Only scan command-bearing fields, plus any field whose name
        # contains one of the keywords (covers nested shapes like
        # {"shell": {"command": "..."}}).
        if key_l in _COMMAND_FIELD_NAMES or any(
            name in key_l for name in _COMMAND_FIELD_NAMES
        ):
            for pat in _DANGEROUS_PATTERNS:
                if pat.search(value):
                    return pat.pattern
    return None


def is_destructive_command(command: str) -> bool:
    """Quick check: does a single command string contain a destructive
    pattern? Useful for callers that already know they have a command
    string and don't want to inspect the whole argument dict."""
    if not command:
        return False
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(command):
            return True
    return False
