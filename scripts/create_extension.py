#!/usr/bin/env python3
"""Create a minimal installable Agent Platform extension."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def create_extension(extension_id: str, name: str, output_root: Path) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", extension_id):
        raise ValueError("extension_id must be a dotted identifier")
    target = output_root / extension_id.replace(".", "_")
    if target.exists():
        raise FileExistsError(f"extension already exists: {target}")
    (target / "frontend").mkdir(parents=True)
    tool_id = f"{extension_id}.inspect"
    api_route = f"/api/extensions/{extension_id}/status"
    page_route = f"/extensions/{extension_id}/overview"
    manifest = {
        "extension_id": extension_id,
        "name": name,
        "version": "0.1.0",
        "api_version": "1",
        "min_platform_version": "1.0.0",
        "capabilities": [extension_id.replace(".", "_")],
        "tools": [tool_id],
        "permissions": ["workspace:read"],
        "routes": [api_route],
        "frontend_modules": ["frontend/Overview.tsx"],
        "frontend_routes": [{
            "path": page_route,
            "module": "frontend/Overview.tsx",
            "label": name,
            "order": 100,
        }],
        "entrypoint": "backend.py:register",
    }
    (target / "extension.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (target / "backend.py").write_text(
        f'''from flask import jsonify, request\n\n\ndef inspect(invocation):\n    return {{"ok": True, "summary": "{name} is ready", "workspace_id": invocation.workspace_id}}\n\n\ndef register_routes(app):\n    @app.route("{api_route}")\n    def status():\n        return jsonify({{"ok": True, "workspace_id": request.args.get("workspace_id", "")}})\n\n\ndef register():\n    return {{\n        "tools": [{{\n            "tool_id": "{tool_id}",\n            "name": "{name}",\n            "category": "general",\n            "permission_action": "read",\n            "input_schema": {{"type": "object", "properties": {{}}}},\n            "handler": inspect,\n        }}],\n        "register_routes": register_routes,\n    }}\n''',
        encoding="utf-8",
    )
    (target / "frontend" / "Overview.tsx").write_text(
        f'''export default function Overview() {{\n  return <div className="page"><header className="page-header"><h1>{name}</h1></header><div className="page-body">扩展已接入。</div></div>;\n}}\n''',
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extension_id")
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", default="plugins")
    args = parser.parse_args()
    target = create_extension(args.extension_id, args.name, Path(args.output))
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
