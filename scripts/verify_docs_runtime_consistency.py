#!/usr/bin/env python3
"""Validate current documentation against current runtime surfaces."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    marker = "PASS" if condition else "FAIL"
    print(f"[{marker}] {message}")
    if not condition:
        failures.append(message)


def read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def markdown_links(text: str) -> list[str]:
    return [
        target
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
        if "://" not in target and not target.startswith("#")
    ]


def documentation_contract(text: str) -> dict[str, str]:
    """Extract the code-backed contract table from the Loop Engineering doc."""
    return {
        key: value
        for key, value in re.findall(r"^\| `([^`]+)` \| `([^`]+)` \|", text, re.MULTILINE)
    }


def main() -> int:
    from core.tools.manifest_registry import MANIFESTS
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    from core.runtime_engine.goal_loop import (
        DEFAULT_MAX_RECOVERY_ATTEMPTS,
        GOAL_LOOP_STATUSES,
        MAX_GOAL_LOOP_OBSERVATIONS,
        MAX_RECOVERY_TARGET_TEXT,
    )
    from core.runtime_engine.recovery_goals import DEFAULT_MAX_RECOVERY_FINAL_REPLANS
    from core.runtime_engine.recovery_strategy import DEFAULT_RECOVERY_STRATEGIES
    from extensions.network_operations.device_drivers import DEFAULT_PROMPT_SETTLE_SECONDS

    # v3.9.2: 21-tool Codex-style registry; v3.9.13 added
    # The dynamic
    # assertion catches accidental drift without pinning the number.
    _registered = len(CANONICAL_REGISTRY)
    _manifests = len(MANIFESTS)
    check(
        _registered == _manifests and _registered >= 16,
        f"canonical/manifest registry count drift "
        f"(CANONICAL_REGISTRY={_registered}, MANIFESTS={_manifests})",
    )

    required_docs = [
        "README.md",
        "AGENTS.md",
        "DESIGN.md",
        "STRUCTURE.md",
        "docs/API.md",
        "docs/ARCHITECTURE.md",
        "docs/FRONTEND.md",
        "docs/LOOP_ENGINEERING.md",
        "docs/backend/API_CONTRACT.md",
        "docs/storage/STORAGE_BOUNDARIES.md",
    ]
    for path in required_docs:
        check((ROOT / path).is_file(), f"{path} exists")

    readme = read("README.md")
    for target in markdown_links(readme):
        check((ROOT / target).exists(), f"README link exists: {target}")

    loop_contract = documentation_contract(read("docs/LOOP_ENGINEERING.md"))
    expected_loop_contract = {
        "DEFAULT_MAX_RECOVERY_ATTEMPTS": str(DEFAULT_MAX_RECOVERY_ATTEMPTS),
        "DEFAULT_MAX_RECOVERY_FINAL_REPLANS": str(DEFAULT_MAX_RECOVERY_FINAL_REPLANS),
        "MAX_RECOVERY_TARGET_TEXT": str(MAX_RECOVERY_TARGET_TEXT),
        "MAX_GOAL_LOOP_OBSERVATIONS": str(MAX_GOAL_LOOP_OBSERVATIONS),
        "GOAL_LOOP_STATUSES": ",".join(GOAL_LOOP_STATUSES),
        "DEFAULT_RECOVERY_STRATEGIES": ",".join(DEFAULT_RECOVERY_STRATEGIES.strategy_ids),
        "DEFAULT_PROMPT_SETTLE_SECONDS": str(DEFAULT_PROMPT_SETTLE_SECONDS),
    }
    for key, expected in expected_loop_contract.items():
        check(
            loop_contract.get(key) == expected,
            f"Loop Engineering doc matches code contract: {key}={expected}",
        )

    combined_docs = "\n".join(read(path) for path in required_docs)
    design = read("DESIGN.md")
    production_compose = read("deployment/compose.production.yml")
    check("当前 13 个能力" not in design, "DESIGN does not pin a stale capability count")
    check("tool_execution_outcome" in design, "DESIGN separates task and tool outcomes")
    check("LZCORE_EVENT_BUS_MODE: redis" in production_compose, "production profile enables Redis event bus")
    required_current_refs = [
        "/api/agent/message",
        "WebSocket",
        "Zustand",
        # v3.9.14: removed "Virtuoso" — the frontend dropped the
        # Virtuoso virtual-list dependency when the Run History panel
        # was rewritten in v3.9.x. We do not require the dead term
        # to appear in docs any more.
        "manifest_registry.py",
        "workspace_id",
        "goal_loop",
        "runtime_recoveries",
        "plan_goal_ids",
    ]
    for reference in required_current_refs:
        check(reference in combined_docs, f"documents current surface: {reference}")

    structure = read("STRUCTURE.md")
    forbidden_current_tree_rows = (
        "\n├── data/",
        "\n├── runtime/",
        "\n├── workspace/",
        "`workspaces/`, `data/`",
    )
    for marker in forbidden_current_tree_rows:
        check(marker not in structure, f"STRUCTURE omits removed root: {marker}")

    for removed_root in ("data", "runtime", "workspace"):
        check(not (ROOT / removed_root).exists(), f"removed root absent: {removed_root}/")

    removed_cipher = "HMAC" + " + " + "XOR"
    check(removed_cipher not in combined_docs, "documents omit removed credential cipher")
    check("AES-GCM" in combined_docs, "documents current credential encryption")

    print(
        f"\n{len(failures)} failure(s)"
        if failures
        else "\nDocumentation and runtime surfaces are consistent."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
