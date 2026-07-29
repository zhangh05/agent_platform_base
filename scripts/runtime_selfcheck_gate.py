#!/usr/bin/env python3
"""CI Selfcheck Gate — exit 0 if healthy/warning, exit 1 if degraded/failed.

Usage:
    python scripts/runtime_selfcheck_gate.py              # default workspace
    python scripts/runtime_selfcheck_gate.py --workspace test_ws
    python scripts/runtime_selfcheck_gate.py --fail-on-warning
    python scripts/runtime_selfcheck_gate.py --json
"""

import argparse
import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.runtime.selfcheck import run_selfcheck, SelfcheckStatus


def main():
    parser = argparse.ArgumentParser(description="CI Runtime Selfcheck Gate")
    parser.add_argument("--workspace", default="default", help="Workspace ID")
    parser.add_argument("--fail-on-warning", action="store_true",
                        help="Treat warnings as failures")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of text")
    args = parser.parse_args()

    result = run_selfcheck(args.workspace)

    # Build safe output (no absolute paths, no secrets)
    output = {
        "workspace_id": args.workspace,
        "status": result.status,
        "issue_count": len(result.issues),
        "issues": [i.as_dict() for i in result.issues],
    }

    # Redact any accidental absolute paths
    output_str = json.dumps(output, default=str)
    if "/Users/" in output_str:
        output_str = output_str.replace("/Users/", "[PATH_REDACTED]/")
    if "/home/" in output_str:
        output_str = output_str.replace("/home/", "[PATH_REDACTED]/")

    if args.json:
        print(output_str)
    else:
        print(f"Selfcheck: {result.status}")
        for issue in result.issues:
            prefix = {"critical": "CRIT", "error": "ERR", "warning": "WARN", "info": "INFO"}
            print(f"  [{prefix.get(issue.severity, issue.severity)}] {issue.code}: {issue.message}")

    # Determine exit code
    if result.status == SelfcheckStatus.FAILED or result.status == SelfcheckStatus.DEGRADED:
        if not args.json:
            print(f"\nGate FAILED: {result.status}")
        sys.exit(1)

    if args.fail_on_warning and result.status == SelfcheckStatus.WARNING:
        if not args.json:
            print("\nGate FAILED (--fail-on-warning): warning status")
        sys.exit(1)

    if not args.json:
        print("\nGate PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
