#!/usr/bin/env python3
"""Stage, activate, and roll back immutable LZCore releases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deployment.slots import ReleaseError, activate_release, rollback_release, stage_release, verify_health


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root")
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("version")
    stage.add_argument("--source", default=str(ROOT))
    activate = commands.add_parser("activate")
    activate.add_argument("version")
    activate.add_argument("--health-url")
    activate.add_argument("--timeout", type=float, default=10)
    commands.add_parser("rollback")
    args = parser.parse_args()
    try:
        if args.command == "stage":
            result = stage_release(args.source, args.version, root=args.release_root)
        elif args.command == "activate":
            result = activate_release(args.version, root=args.release_root)
            if args.health_url:
                try:
                    result["health"] = verify_health(args.health_url, args.timeout)
                except ReleaseError:
                    rollback_release(root=args.release_root)
                    raise
        else:
            result = rollback_release(root=args.release_root)
    except ReleaseError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
