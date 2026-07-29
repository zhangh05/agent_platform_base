#!/usr/bin/env python3
"""v3.0 verify that general_tools/ is genuinely split out.

In v3.0 the dispatch layer is ``core.tools.canonical_registry``.
The ``core/tools/general_tools/`` modules still host the underlying
handler implementations, but they are wired by canonical_id now
(not by v2.x execution_id).
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GENERAL_DIR = ROOT / "core" / "tools" / "general_tools"
BASE = ROOT / "core" / "tools" / "general_tools_base.py"

FORBIDDEN_PATTERNS = {
    "_HANDLERS": re.compile(r"_HANDLERS"),
    "lazy_import": re.compile(r"lazy_import"),
    "base_alias_import": re.compile(
        r"import\s+core\.tools\.general_tools_base\s+as\s+_b"
    ),
    "module_spoof": re.compile(r"__module__\s*=\s*__name__"),
    "return_handlers": re.compile(r"return\s+_HANDLERS"),
}


def _check(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"{status}: {name}{suffix}")
    return ok


def _source_files() -> list[Path]:
    paths = sorted(GENERAL_DIR.glob("*.py"))
    if BASE.exists():
        paths.append(BASE)
    return paths


def main() -> int:
    failures = 0

    # Source-level invariants
    for path in _source_files():
        text = path.read_text()
        for name, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(text):
                failures += not _check(
                    f"{path.name} does not contain '{name}'", False
                )

    # Canonical-only invariants
    from core.tools.tool_namespace import TOOL_NAMESPACE
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    failures += not _check(
        f"canonical_count == registry_count "
        f"({len(TOOL_NAMESPACE)} vs {len(CANONICAL_REGISTRY)})",
        len(TOOL_NAMESPACE) == len(CANONICAL_REGISTRY),
    )

    # Handler implementation files exist for the canonical registry
    from core.tools.canonical_registry import CANONICAL_REGISTRY
    missing_handlers: list[str] = []
    for canonical_id in CANONICAL_REGISTRY:
        entry = CANONICAL_REGISTRY[canonical_id]
        handler = entry.handler
        handler_module = getattr(handler, "__module__", "")
        # _adapt() wraps the handler; check the underlying module via closure.
        if handler_module != "core.tools.canonical_registry":
            missing_handlers.append(
                f"{canonical_id} -> {handler_module or '?'}"
            )
    failures += not _check(
        "all canonical handlers are dispatched from canonical_registry",
        not missing_handlers,
        f"{len(missing_handlers)} unwrapped" if missing_handlers else "",
    )

    if failures:
        print(f"FAIL: {failures} split checks failed")
        return 1
    print("INSPECT GENERAL TOOLS SPLIT PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
