#!/usr/bin/env python3
"""Storage doctor CLI — read-only health checks."""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.doctor import run_doctor
from storage.paths import get_workspace_root


def main():
    parser = argparse.ArgumentParser(description="Storage doctor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--workspace", type=str, help="Check one workspace")
    group.add_argument("--all", action="store_true", help="Check all workspaces")
    args = parser.parse_args()

    if args.all:
        root = get_workspace_root()
        results = []
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if d.is_dir() and re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", d.name):
                    results.append(run_doctor(d.name))
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(run_doctor(args.workspace), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
