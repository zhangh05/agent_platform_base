#!/usr/bin/env python3
"""Focused extension compatibility gate for CI and release builds."""

from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from extensions.registry import ExtensionRegistry
from extensions.runtime import load_extensions


def main() -> int:
    errors, manifests = ExtensionRegistry().validate_all()
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    loaded = load_extensions(refresh=True)
    print(json.dumps({
        "ok": True,
        "manifests": [{"extension_id": item.extension_id, "version": item.version} for item in manifests],
        "loaded": [item.manifest.extension_id for item in loaded],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
