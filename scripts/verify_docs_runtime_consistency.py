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


def main() -> int:
    from core.tools.manifest_registry import MANIFESTS
    from core.tools.canonical_registry import CANONICAL_REGISTRY

    # v3.9.2: 21-tool Codex-style registry; v3.9.13 added
    # The dynamic
    # assertion catches accidental drift without pinning the number.
    _registered = len(CANONICAL_REGISTRY)
    _manifests = len(MANIFESTS)
    check(
        _registered == _manifests and _registered >= 20,
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
        "docs/backend/API_CONTRACT.md",
        "docs/storage/STORAGE_BOUNDARIES.md",
    ]
    for path in required_docs:
        check((ROOT / path).is_file(), f"{path} exists")

    readme = read("README.md")
    for target in markdown_links(readme):
        check((ROOT / target).exists(), f"README link exists: {target}")

    combined_docs = "\n".join(read(path) for path in required_docs)
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
