"""Run deterministic golden cases through the governed ToolRuntime pipeline."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ["NA_WORKSPACE_ROOT"] = tempfile.mkdtemp(prefix="agent-platform-eval-")

from core.tools.context import ToolRuntimeContext
from core.tools.integration import get_default_tool_runtime_client
from storage.workspace_store import ensure_workspace


def main() -> int:
    ensure_workspace("eval")
    context = ToolRuntimeContext(workspace_id="eval", requested_by="turn_runner", module="platform_eval")
    client = get_default_tool_runtime_client()
    cases = [
        ("workspace-metadata", "workspace.metadata.get", {"workspace_id": "eval"}),
        ("text-redaction", "text.analyze", {"action": "redact", "text": "password=secret-value"}),
    ]
    results = []
    for case_id, tool_id, arguments in cases:
        result = client.invoke(tool_id, arguments, context=context)
        results.append({"case_id": case_id, "tool_id": tool_id, "passed": result.status == "succeeded", "status": result.status})
    report = {"total": len(results), "passed": sum(item["passed"] for item in results), "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
