#!/usr/bin/env python3
"""Enforce the LZCore engineering and 联智中枢 product naming contract."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Build retired identities from fragments so the guard does not exempt itself
# or keep obsolete names as active, searchable source text.
RETIRED_IDENTITIES = {
    "legacy repository snake case": "agent" + "_platform_base",
    "legacy repository kebab case": "agent" + "-platform-base",
    "legacy product title": "Agent" + " Platform Base",
    "legacy runtime snake case": "agent" + "_platform",
    "legacy runtime kebab case": "agent" + "-platform",
    "legacy alert prefix": "Agent" + "Platform",
    "legacy environment prefix": "AGENT" + "_PLATFORM_",
    "legacy workspace variable": "NA_" + "WORKSPACE_ROOT",
    "legacy token variable": "NA_" + "API_TOKEN",
    "retired product name": "Network" + " Agent",
}

# These markers assert the naming *semantics*, not a particular language or
# sentence. Documentation is intentionally Chinese-first, so a wording edit
# must not force a stale English heading back into the repository.
REQUIRED_TEXT = {
    "README.md": ("# 联智中枢", "LZCore", "lzcore"),
    "AGENTS.md": ("## 命名", "联智中枢", "LZCore", "lzcore"),
    "frontend/index.html": ("<title>联智中枢</title>",),
    "frontend/package.json": ('"name": "lzcore-workbench"',),
    "core/runtime_engine/prompt_contract.py": ("You are 联智中枢",),
    "deployment/observability/alerts.yml": (
        "LZCoreTargetDown",
        "LZCoreHighHttp5xxRate",
        "LZCoreToolFailures",
        "LZCoreJobFailures",
    ),
    "deployment/observability/grafana-provisioning/dashboards/json/lzcore.json": (
        '"title": "联智中枢运行总览"',
    ),
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def main() -> int:
    failures: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT)
        relative_text = relative.as_posix()
        for label, retired in RETIRED_IDENTITIES.items():
            if retired in relative_text:
                failures.append(f"{relative}: path contains {label}")
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, retired in RETIRED_IDENTITIES.items():
            if retired in text:
                failures.append(f"{relative}: contains {label}")

    for relative, markers in REQUIRED_TEXT.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"{relative}: required naming surface is missing")
            continue
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                failures.append(f"{relative}: missing required naming marker {marker!r}")

    if failures:
        print("Naming contract violations:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Naming contract is consistent: product=联智中枢, framework=LZCore, slug=lzcore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
