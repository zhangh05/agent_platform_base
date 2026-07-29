"""
Dangerous command detection for exec.run shell execution.

OpenCode-level safety: hard-block patterns, destructive command warnings,
command substitution detection, and high-impact operational guards.

Architecture:
    check_dangerous(command: str) -> tuple[bool, str | None]
        Returns (is_dangerous, reason) — fast regex scan, no AST needed.
    DANGER_LEVELS:
        BLOCK  — command is rejected outright (hard forbidden).
        WARN   — command is allowed but emits a warning to the user.
"""

from __future__ import annotations

import re

# ──────────────────────────────────────────────────────────────────────
# Danger levels
# ──────────────────────────────────────────────────────────────────────

BLOCK = "block"   # Reject outright — never execute
WARN = "warn"     # Allow but emit warning

# ──────────────────────────────────────────────────────────────────────
# Hard-blocked patterns — these commands are NEVER executed
# ──────────────────────────────────────────────────────────────────────

# Pattern format: (compiled_regex, level, human_reason)
# Order matters: broader patterns first, narrower later for override

_BLOCK_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ── Filesystem destruction ──
    (re.compile(r"\brm\s+-rf\s+/", re.IGNORECASE), "rm -rf / would destroy the system"),
    (re.compile(r"\brm\s+-rf\s+--no-preserve-root", re.IGNORECASE), "rm -rf --no-preserve-root would destroy the system"),
    (re.compile(r"\bdd\s+if=/dev/zero\s+of=/dev/", re.IGNORECASE), "dd to block device would destroy data"),
    (re.compile(r"\bdd\s+if=/dev/urandom\s+of=/dev/", re.IGNORECASE), "dd to block device would destroy data"),
    (re.compile(r"\bmkfs\.", re.IGNORECASE), "mkfs would format a filesystem"),
    (re.compile(r"\bmke2fs\b", re.IGNORECASE), "mke2fs would format a filesystem"),

    # ── Global chmod ──
    (re.compile(r"\bchmod\s+(-R\s+)?777\s+/", re.IGNORECASE), "chmod 777 / is a severe security risk"),

    # ── Fork bombs ──
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"), "fork bomb detected"),
    (re.compile(r"\bperl\s+-e\s+.*fork.*fork", re.IGNORECASE), "perl fork bomb detected"),

    # ── Device destructive operations ──
    (re.compile(r"\breload\b", re.IGNORECASE), "reload would reboot the device"),
    (re.compile(r"\bformat\s+flash", re.IGNORECASE), "format flash would wipe device storage"),
    (re.compile(r"\berase\s+startup-config", re.IGNORECASE), "erase startup-config would wipe device config"),
    (re.compile(r"\bwrite\s+erase\b", re.IGNORECASE), "write erase would delete device config"),
    (re.compile(r"\brequest\s+system\s+zeroize\b", re.IGNORECASE), "request system zeroize would factory-reset a device"),
    (re.compile(r"\breset\s+saved-configuration\b", re.IGNORECASE), "reset saved-configuration would wipe device state"),

    # ── Privilege escalation ──
    (re.compile(r"\bsudo\s+su\b", re.IGNORECASE), "sudo su would escalate privileges"),
    (re.compile(r"\bchown\s+-R\s+\w+\s+/", re.IGNORECASE), "recursive chown on / would break the system"),

    # ── System shutdown ──
    (re.compile(r"\b(shutdown|halt|poweroff|reboot)\s+(-h\s+)?now\b", re.IGNORECASE), "system shutdown command detected"),
    (re.compile(r"\binit\s+[06]\b", re.IGNORECASE), "init 0/6 would shutdown/reboot"),
]

# ──────────────────────────────────────────────────────────────────────
# Warning patterns — allowed but with user warning
# ──────────────────────────────────────────────────────────────────────

_WARN_PATTERNS: list[tuple[re.Pattern, str]] = [
    # ── Git destructive ──
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard discards uncommitted changes"),
    (re.compile(r"\bgit\s+push\s+.*(--force|--force-with-lease|-f)\b"), "force push may overwrite remote history"),
    (re.compile(r"\bgit\s+clean\s+-[a-z]*f[a-z]*"), "git clean -f removes untracked files"),

    # ── Kubernetes destructive ──
    (re.compile(r"\bkubectl\s+delete\b"), "kubectl delete removes cluster resources"),
    (re.compile(r"\bkubectl\s+drain\b"), "kubectl drain evicts workloads"),
    (re.compile(r"\bhelm\s+uninstall\b"), "helm uninstall removes a release"),

    # ── Infrastructure ──
    (re.compile(r"\bterraform\s+destroy\b"), "terraform destroy tears down infrastructure"),
    (re.compile(r"\bterraform\s+apply\s+.*-auto-approve\b"), "terraform apply -auto-approve skips confirmation"),

    # ── Database destructive ──
    (re.compile(r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE), "DROP/TRUNCATE destroys data"),
    (re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE), "DELETE FROM removes rows without confirmation"),

    # ── Docker destructive ──
    (re.compile(r"\bdocker\s+system\s+prune\b"), "docker system prune removes unused objects"),
    (re.compile(r"\bdocker\s+rmi?\s+-f\b"), "docker rmi -f / rm -f force-removes"),

    # ── Network device warnings ──
    (re.compile(r"\bshutdown\b", re.IGNORECASE), "'shutdown' may disable an interface on network devices"),
    (re.compile(r"\bno\s+router\s+bgp\b", re.IGNORECASE), "removing BGP configuration disrupts routing"),

    # ── Filesystem risky ──
    (re.compile(r"\brm\s+-rf\b"), "rm -rf removes files recursively"),
    (re.compile(r"\bchmod\s+777\b"), "chmod 777 opens files to all users"),
    (re.compile(r"\bchown\s+-R\b"), "recursive chown changes ownership broadly"),
]

# ──────────────────────────────────────────────────────────────────────
# Command substitution / injection patterns (suspicious but not blocking)
# ──────────────────────────────────────────────────────────────────────

_SUSPICIOUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\$\(.*rm\s+"), "$(rm ...) command substitution with destructive intent"),
    (re.compile(r"`[^`]*rm\s+"), "backtick command substitution with rm"),
    (re.compile(r"\$\{[^}]*rm\s+"), "${} parameter expansion with rm"),
    (re.compile(r"<\("), "process substitution <() detected"),
    (re.compile(r">\("), "process substitution >() detected"),
    (re.compile(r"\|\s*sh\b"), "pipe to sh (code execution via pipeline)"),
    (re.compile(r"\|\s*bash\b"), "pipe to bash (code execution via pipeline)"),
    (re.compile(r"curl.*\|\s*(sh|bash)\b"), "curl | sh pattern (remote code execution)"),
    (re.compile(r"wget.*\|\s*(sh|bash)\b"), "wget | sh pattern (remote code execution)"),
    (re.compile(r"eval\s+"), "eval executes arbitrary string as code"),
    (re.compile(r"\bsource\s+/dev/"), "sourcing from /dev/ filesystem"),
]


def check_dangerous(command: str) -> tuple[bool, str | None]:
    """Check a command for dangerous patterns.

    Returns:
        (is_dangerous, reason) — True means the command should be rejected.
        reason provides a human-readable explanation.
    """
    if not command or not isinstance(command, str):
        return False, None

    cmd = command.strip()

    # ── Phase 1: Hard-block patterns — reject immediately ──
    for pattern, reason in _BLOCK_PATTERNS:
        if pattern.search(cmd):
            return True, f"[BLOCKED] {reason}"

    return False, None


def check_warnings(command: str) -> list[str]:
    """Check a command for warning-level patterns.

    Returns:
        List of warning strings. Empty list means no warnings.
    """
    if not command or not isinstance(command, str):
        return []

    cmd = command.strip()
    warnings: list[str] = []

    for pattern, reason in _WARN_PATTERNS:
        if pattern.search(cmd):
            warnings.append(f"[WARNING] {reason}")

    return warnings


def check_suspicious(command: str) -> list[str]:
    """Check for suspicious patterns (injection, command substitution).

    Returns:
        List of suspicious pattern descriptions. Empty list means clean.
    """
    if not command or not isinstance(command, str):
        return []

    cmd = command.strip()
    findings: list[str] = []

    for pattern, reason in _SUSPICIOUS_PATTERNS:
        if pattern.search(cmd):
            findings.append(f"[SUSPICIOUS] {reason}")

    return findings


def full_check(command: str) -> dict:
    """Run all checks and return a comprehensive result.

    Returns:
        {
            "safe": bool,
            "blocked": bool,
            "reason": str | None,     # if blocked, the reason
            "warnings": list[str],    # warning-level issues
            "suspicious": list[str],  # suspicious patterns found
        }
    """
    blocked, reason = check_dangerous(command)
    if blocked:
        return {
            "safe": False,
            "blocked": True,
            "reason": reason,
            "warnings": [],
            "suspicious": [],
        }

    warnings = check_warnings(command)
    suspicious = check_suspicious(command)

    return {
        "safe": True,
        "blocked": False,
        "reason": None,
        "warnings": warnings,
        "suspicious": suspicious,
    }
