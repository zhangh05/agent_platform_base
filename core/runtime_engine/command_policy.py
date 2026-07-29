"""
Command Policy Engine — v1.0 hardened command normalization and policy evaluation.

Uses a structured multi-stage command check:

  1. normalize_command(raw) → NormalizedCommand
     - Extracts executable, strips .exe, handles Windows paths
     - Identifies shell type (powershell/pwsh/cmd/reg/regedit/diskpart/bcdedit)
     - Preserves args

  2. evaluate_command_policy(normalized) → CommandPolicyDecision
     - Checks executable-level blocks (shutdown, format, diskpart, etc.)
     - Checks arg-level blocks (reg add, reg delete, PowerShell -EncodedCommand, etc.)
     - Enforces PowerShell allowlist (-File only, workspace/scripts/ path)
     - Returns unified decision used by both semantic_validator and risk_policy

Design principle: ONE source of truth for command safety.
Not regex-dependent — based on normalized executable + arg parsing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# Executable-level blocked list (after normalization — no extension, lowercase)
# ============================================================================

BLOCKED_EXECUTABLES: set[str] = {
    "shutdown",
    "reboot",
    "format",
    "diskpart",
    "bcdedit",
    "takeown",
    "cipher",
    "reg",        # reg add / reg delete blocked at arg level
    "regedit",    # regedit /s blocked at arg level
    "regsvr32",
    "rundll32",
    "mshta",
    "wmic",
    "schtasks",
}


# ============================================================================
# PowerShell blocked patterns
# ============================================================================

# PowerShell executables
POWERSHELL_NAMES = {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}

# PowerShell forbidden flags/args (lowercase)
POWERSHELL_FORBIDDEN_FLAGS = {
    "-encodedcommand", "-enc", "-e",
    "-command", "-c",
}

# PowerShell forbidden cmdlets/functions (lowercase)
POWERSHELL_FORBIDDEN_CMDLETS = {
    "remove-item", "rm",
    "invoke-expression", "iex",
    "start-process",
    "set-executionpolicy",
    "add-mppreference",
    "set-mppreference",
    "disablerealtimemonitoring",
}

# PowerShell allowlist: only these flags are permitted for execution
POWERSHELL_ALLOWED_FLAGS = {"-file", "-f"}


# ============================================================================
# Registry tool blocked actions
# ============================================================================

REGISTRY_BLOCKED_ACTIONS = {"add", "delete", "import", "restore", "/s"}


# ============================================================================
# Destructive shell patterns (cmd.exe / bash)
# ============================================================================

DESTRUCTIVE_CMD_PATTERNS: list[str] = [
    r"\bdel\s+/s\b",
    r"\brd\s+/s\b",
    r"\brm\s+-rf\b",
    r"\bdelete\s+recursive\b",
    r"\bdisable\s+firewall\b",
    r"\bdisable\s+antivirus\b",
]


# ============================================================================
# Models
# ============================================================================

@dataclass
class NormalizedCommand:
    """Result of command normalization."""
    raw: str
    executable: str = ""
    executable_base: str = ""
    args: list[str] = field(default_factory=list)
    lower: str = ""
    is_powershell: bool = False
    is_cmd: bool = False
    is_registry_tool: bool = False
    is_disk_tool: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "executable": self.executable,
            "executable_base": self.executable_base,
            "args": self.args,
            "lower": self.lower,
            "is_powershell": self.is_powershell,
            "is_cmd": self.is_cmd,
            "is_registry_tool": self.is_registry_tool,
            "is_disk_tool": self.is_disk_tool,
        }


@dataclass
class CommandPolicyDecision:
    """Result of command policy evaluation."""
    allowed: bool = True
    risk_level: str = "low"
    reason: str = ""
    error_code: str = ""
    normalized: NormalizedCommand | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "error_code": self.error_code,
            "normalized": self.normalized.to_dict() if self.normalized else None,
        }


# ============================================================================
# Helpers
# ============================================================================

def _os_path_basename(path: str) -> str:
    """Get the base name from a path, handling both Unix and Windows separators."""
    # Handle both separator types
    path = path.replace("\\", "/")
    return path.split("/")[-1]


# ============================================================================
# Normalization
# ============================================================================

def normalize_command(raw: str) -> NormalizedCommand:
    """Normalize a raw command string into structured NormalizedCommand.

    Steps:
      1. Strip whitespace, lowercase
      2. Extract executable (handle quoted paths)
      3. Strip .exe suffix, extract base name from Windows paths
      4. Identify shell type
      5. Extract args
    """
    raw = raw or ""
    lower = raw.strip().lower()

    nc = NormalizedCommand(raw=raw, lower=lower)

    if not lower:
        return nc

    # Extract executable: handle both quoted and unquoted paths
    # e.g. "C:\Program Files\app.exe" arg1 arg2 → executable = "C:\Program Files\app.exe", rest = "arg1 arg2"
    exe = ""
    rest = lower
    if lower.startswith('"'):
        # Quoted executable
        end_quote = lower.find('"', 1)
        if end_quote != -1:
            exe = lower[1:end_quote]
            rest = lower[end_quote + 1:].strip()
    else:
        # Space-separated
        parts = lower.split(None, 1)
        exe = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

    nc.executable = exe

    # Normalize: strip .exe, get base name from path
    base = _os_path_basename(exe)
    if base.endswith(".exe"):
        base = base[:-4]
    nc.executable_base = base

    # Parse args
    if rest:
        nc.args = _parse_args(rest)
    else:
        nc.args = []

    # Identify shell type
    nc.is_powershell = base in ("powershell", "pwsh")
    nc.is_cmd = base in ("cmd", "cmd.exe") or bool(re.search(r"\bcmd\b", lower))
    nc.is_registry_tool = base in ("reg", "regedit", "regsvr32") or (nc.args and nc.args[0] in REGISTRY_BLOCKED_ACTIONS)
    nc.is_disk_tool = base in ("diskpart", "format", "bcdedit", "chkdsk")

    return nc


def _parse_args(rest: str) -> list[str]:
    """Simple arg parser: split on whitespace and flags."""
    tokens = []
    current = ""
    in_quote = False

    for ch in rest:
        if ch == '"':
            in_quote = not in_quote
            current += ch
        elif ch == ' ' and not in_quote:
            if current:
                tokens.append(current.lower())
                current = ""
        else:
            current += ch

    if current:
        tokens.append(current.lower())

    return tokens


# ============================================================================
# Policy Evaluation
# ============================================================================

def evaluate_command_policy(normalized: NormalizedCommand) -> CommandPolicyDecision:
    """Evaluate whether a normalized command should be allowed.

    Called by both semantic_validator and risk_policy for unified decision.

    Returns:
        CommandPolicyDecision with allowed=False if blocked.
    """
    base = normalized.executable_base
    args = normalized.args
    lower = normalized.lower

    # ================================================================
    # 1. Executable-level blocks
    # ================================================================
    if base in BLOCKED_EXECUTABLES:
        # For reg/regedit, check args below
        if base not in ("reg", "regedit"):
            return CommandPolicyDecision(
                allowed=False,
                risk_level="high",
                reason=f"Executable '{base}' is blocked by policy",
                error_code="FORBIDDEN_COMMAND",
                normalized=normalized,
            )

    # ================================================================
    # 2. Registry tool arg-level blocks
    # ================================================================
    if base in ("reg", "regedit"):
        if args:
            first_arg = args[0].lower().strip('"')
            if first_arg in REGISTRY_BLOCKED_ACTIONS:
                return CommandPolicyDecision(
                    allowed=False,
                    risk_level="high",
                    reason=f"Registry operation '{base} {first_arg}' is blocked by policy",
                    error_code="FORBIDDEN_COMMAND",
                    normalized=normalized,
                )

    # ================================================================
    # 3. PowerShell-specific checks
    # ================================================================
    if normalized.is_powershell:
        decision = _evaluate_powershell(normalized)
        if not decision.allowed:
            return decision

    # ================================================================
    # 4. Destructive cmd patterns (regex-based fallback for cmd/bash)
    # ================================================================
    for pattern in DESTRUCTIVE_CMD_PATTERNS:
        if re.search(pattern, lower):
            return CommandPolicyDecision(
                allowed=False,
                risk_level="high",
                reason=f"Destructive command pattern matched: '{pattern}'",
                error_code="FORBIDDEN_COMMAND",
                normalized=normalized,
            )

    # ================================================================
    # 5. PowerShell cmdlet detection in non-powershell context
    #    (e.g., "Remove-Item -Recurse C:\" as a raw command)
    # ================================================================
    if not normalized.is_powershell and base:
        # Check if the first "word" is a PowerShell cmdlet
        first_token = base
        if first_token in POWERSHELL_FORBIDDEN_CMDLETS:
            return CommandPolicyDecision(
                allowed=False,
                risk_level="critical",
                reason=f"PowerShell cmdlet '{first_token}' is blocked in direct execution",
                error_code="FORBIDDEN_COMMAND",
                normalized=normalized,
            )

    # Allow
    return CommandPolicyDecision(
        allowed=True,
        risk_level="low",
        normalized=normalized,
    )


def _evaluate_powershell(nc: NormalizedCommand) -> CommandPolicyDecision:
    """PowerShell-specific policy evaluation.

    Allow:
      powershell -File workspace/scripts/allowed.ps1

    Block:
      -EncodedCommand, -Command, -c, -enc, -e
      Remove-Item, Invoke-Expression, IEX, Start-Process
      Path traversal (..)
      System directory scripts (C:\\Windows, C:\\WINDOWS)
    """
    args = nc.args
    args_lower = [a.lower() for a in args]

    # Check for forbidden flags
    for arg in args_lower:
        clean_arg = arg.strip('"').strip("'")
        if clean_arg in POWERSHELL_FORBIDDEN_FLAGS:
            return CommandPolicyDecision(
                allowed=False,
                risk_level="critical",
                reason=f"PowerShell flag '{arg}' is blocked (only -File allowed)",
                error_code="FORBIDDEN_COMMAND",
                normalized=nc,
            )

    # Check for forbidden cmdlets via inline args
    for arg in args_lower:
        clean_arg = arg.strip('"').strip("'")
        for cmdlet in POWERSHELL_FORBIDDEN_CMDLETS:
            if clean_arg == cmdlet or clean_arg.startswith(cmdlet):
                return CommandPolicyDecision(
                    allowed=False,
                    risk_level="critical",
                    reason=f"PowerShell cmdlet '{cmdlet}' is blocked in any execution context",
                    error_code="FORBIDDEN_COMMAND",
                    normalized=nc,
                )

    # Check if -File flag is present
    has_file_flag = any(a.lower() in POWERSHELL_ALLOWED_FLAGS for a in args)
    if not has_file_flag:
        # If no -File flag and no forbidden flag was found above,
        # still check if this looks like an inline command
        if any(a.lower().startswith("-") for a in args):
            return CommandPolicyDecision(
                allowed=False,
                risk_level="high",
                reason="PowerShell requires -File flag; -Command and other inline modes are blocked",
                error_code="FORBIDDEN_COMMAND",
                normalized=nc,
            )

    # If -File flag is present, validate the script path
    if has_file_flag:
        file_idx = None
        for i, a in enumerate(args_lower):
            if a in POWERSHELL_ALLOWED_FLAGS:
                file_idx = i + 1
                break

        if file_idx is not None and file_idx < len(args):
            script_path = args[file_idx].strip('"').strip("'")

            # Block path traversal
            path_components = script_path.replace("\\", "/").split("/")
            if ".." in path_components:
                return CommandPolicyDecision(
                    allowed=False,
                    risk_level="high",
                    reason=f"PowerShell script path contains traversal: '{script_path}'",
                    error_code="FORBIDDEN_COMMAND",
                    normalized=nc,
                )

            # Block system directory scripts
            system_prefixes = ["c:\\windows\\", "c:\\windows\\system32\\",
                               "/etc/", "/system/", "/boot/", "c:\\windows\\temp\\"]
            path_lower = script_path.lower()
            for prefix in system_prefixes:
                if path_lower.startswith(prefix):
                    return CommandPolicyDecision(
                        allowed=False,
                        risk_level="high",
                        reason=f"PowerShell script in system directory: '{script_path}'",
                        error_code="FORBIDDEN_COMMAND",
                        normalized=nc,
                    )

            # Allow: path starts with workspace/scripts/ or scripts/
            if not (path_lower.startswith("workspace/scripts/") or path_lower.startswith("scripts/")):
                return CommandPolicyDecision(
                    allowed=False,
                    risk_level="high",
                    reason=f"PowerShell script path not in workspace/scripts/: '{script_path}'",
                    error_code="FORBIDDEN_COMMAND",
                    normalized=nc,
                )

    return CommandPolicyDecision(
        allowed=True,
        risk_level="low",
        normalized=nc,
    )
