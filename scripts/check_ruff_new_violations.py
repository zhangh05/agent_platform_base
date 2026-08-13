#!/usr/bin/env python3
"""Fail when changed Python lines introduce selected exception-policy debt."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict

RULES = "BLE001,S110,S112"


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout


def _base_revision() -> str:
    candidate = os.environ.get("QUALITY_BASE", "").strip()
    if candidate and set(candidate) != {"0"}:
        probe = subprocess.run(
            ["git", "cat-file", "-e", f"{candidate}^{{commit}}"],
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    if _git("status", "--porcelain").strip():
        return "HEAD"
    parent = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^"],
        capture_output=True,
        text=True,
        check=False,
    )
    return parent.stdout.strip() if parent.returncode == 0 else "HEAD"


def _changed_lines(base: str) -> tuple[dict[str, set[int]], set[str]]:
    changed: dict[str, set[int]] = defaultdict(set)
    current_file = ""
    diff = _git("diff", "--unified=0", "--no-color", base, "--", "*.py")
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if match and current_file:
            start = int(match.group(1))
            count = int(match.group(2) or "1")
            changed[current_file].update(range(start, start + count))
    untracked = {
        item for item in _git("ls-files", "--others", "--exclude-standard", "--", "*.py").splitlines()
        if item
    }
    return changed, untracked


def main() -> int:
    base = _base_revision()
    changed, untracked = _changed_lines(base)
    files = sorted(set(changed) | untracked)
    if not files:
        print("No changed Python files for incremental exception-policy check.")
        return 0
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", RULES, "--output-format", "json", *files],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        violations = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        sys.stderr.write(result.stdout + result.stderr)
        return 2
    introduced = []
    for item in violations:
        filename = os.path.relpath(item["filename"])
        row = int(item["location"]["row"])
        if filename in untracked or row in changed.get(filename, set()):
            introduced.append((filename, row, item["code"], item["message"]))
    if introduced:
        print("New exception-policy violations are not allowed:", file=sys.stderr)
        for filename, row, code, message in introduced:
            print(f"{filename}:{row}: {code} {message}", file=sys.stderr)
        return 1
    print(f"No new {RULES} violations on changed lines (base {base}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
