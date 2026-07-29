#!/usr/bin/env python3
"""v3.0 baseline check: every canonical_tool_id has a handler."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from core.tools.tool_namespace import TOOL_NAMESPACE
    from core.tools.canonical_registry import CANONICAL_REGISTRY

    orphans = set(TOOL_NAMESPACE) - set(CANONICAL_REGISTRY)
    extras = set(CANONICAL_REGISTRY) - set(TOOL_NAMESPACE)
    if orphans or extras:
        if orphans:
            print(f"FAIL  orphans (canonical, no handler): {sorted(orphans)}")
        if extras:
            print(f"FAIL  extras (handler, no canonical): {sorted(extras)}")
        return 1
    print(f"canonical_count:        {len(TOOL_NAMESPACE)}")
    print(f"registry_count:         {len(CANONICAL_REGISTRY)}")
    print(f"matched:                {len(TOOL_NAMESPACE)}")
    print("COMPARE TOOL ID BASELINE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
