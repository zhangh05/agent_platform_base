# scripts/audit_shared.py
"""Shared utilities for audit scripts — reduces boilerplate duplication.

Usage:
    from scripts.audit_shared import AuditRunner, fail, warn, ok

    runner = AuditRunner("audit_artifact_security")
    runner.check("No secrets in artifacts", lambda: ...)
    runner.check("All paths are safe", lambda: ...)
    runner.report()
"""

import sys
import time
from pathlib import Path
from typing import Callable, Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Output helpers ──
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {msg}")


class AuditResult:
    """Result of a single audit check."""
    def __init__(self, name: str, passed: bool, detail: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail


class AuditRunner:
    """Run a series of audit checks and print a summary report.

    Usage:
        runner = AuditRunner("my_audit")
        runner.check("Rule 1", lambda: some_check())
        runner.check("Rule 2", lambda: other_check())
        runner.report()
    """

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.results: list[AuditResult] = []
        self._start = time.monotonic()

    def check(self, name: str, fn: Callable[[], Any], *,
              expect: Any = True,
              detail: str = "") -> bool:
        """Run a check function.

        Args:
            name: Human-readable check name.
            fn: Callable that returns a truthy value on success.
            expect: Value to compare against (default: True).
            detail: Optional detail message on failure.
        Returns:
            True if check passed.
        """
        try:
            result = fn()
            passed = (result == expect) if expect is not True else bool(result)
        except Exception as e:
            passed = False
            if not detail:
                detail = str(e)
        self.results.append(AuditResult(name, passed, detail))
        if passed:
            ok(name)
        else:
            fail(f"{name} — {detail}" if detail else name)
        return passed

    def report(self) -> bool:
        """Print summary and return True if all checks passed."""
        elapsed = time.monotonic() - self._start
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed

        print()
        print(f"{BOLD}Audit: {self.name}{RESET}")
        if self.description:
            print(f"  {self.description}")
        print(f"  Checks: {total} | {GREEN}{passed} passed{RESET}"
              + (f" | {RED}{failed} failed{RESET}" if failed else ""))
        print(f"  Time: {elapsed:.2f}s")

        if failed:
            print(f"\n{RED}Failed checks:{RESET}")
            for r in self.results:
                if not r.passed:
                    print(f"  - {r.name}: {r.detail or 'no detail'}")

        all_ok = failed == 0
        print(f"\n  Status: {GREEN}PASS{RESET}" if all_ok else f"\n  Status: {RED}FAIL{RESET}")
        return all_ok


def main_exit(runner: AuditRunner) -> None:
    """Entry point: run report and exit with appropriate code."""
    all_ok = runner.report()
    sys.exit(0 if all_ok else 1)
