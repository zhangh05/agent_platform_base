"""Validate and print the machine-readable platform contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.capabilities.catalog import list_all
from core.tools.tool_namespace import TOOL_NAMESPACE
from extensions.registry import ExtensionRegistry


def build_contract() -> dict:
    errors, extensions = ExtensionRegistry().validate_all()
    return {
        "tool_count": len(TOOL_NAMESPACE),
        "capability_count": len(list_all()),
        "tool_ids": sorted(TOOL_NAMESPACE),
        "capability_ids": sorted(item["capability_id"] for item in list_all()),
        "extensions": [item.to_dict() for item in extensions],
        "extension_errors": errors,
    }


if __name__ == "__main__":
    contract = build_contract()
    print(json.dumps(contract, ensure_ascii=False, indent=2))
    raise SystemExit(1 if contract["extension_errors"] else 0)
