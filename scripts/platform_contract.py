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
from extensions.runtime import load_extensions


def build_contract() -> dict:
    errors, extensions = ExtensionRegistry().validate_all()
    extension_tool_ids: list[str] = []
    if not errors:
        try:
            loaded = load_extensions(refresh=True)
            extension_tool_ids = sorted(
                spec.tool_id for extension in loaded for spec, _handler in extension.tools
            )
        except Exception as exc:
            errors.append(str(exc))
    return {
        "tool_count": len(TOOL_NAMESPACE) + len(extension_tool_ids),
        "core_tool_count": len(TOOL_NAMESPACE),
        "extension_tool_count": len(extension_tool_ids),
        "capability_count": len(list_all()),
        "tool_ids": sorted([*TOOL_NAMESPACE, *extension_tool_ids]),
        "capability_ids": sorted(item["capability_id"] for item in list_all()),
        "extensions": [item.to_dict() for item in extensions],
        "extension_errors": errors,
    }


if __name__ == "__main__":
    contract = build_contract()
    print(json.dumps(contract, ensure_ascii=False, indent=2))
    raise SystemExit(1 if contract["extension_errors"] else 0)
