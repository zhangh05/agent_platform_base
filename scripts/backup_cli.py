#!/usr/bin/env python3
"""Create, list, verify, prune, or restore LZCore backups."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime.backup import backup_path, create_backup, list_backups, prune_backups, restore_backup, verify_backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create")
    commands.add_parser("list")
    verify = commands.add_parser("verify")
    verify.add_argument("archive")
    prune = commands.add_parser("prune")
    prune.add_argument("--keep", type=int, default=10)
    restore = commands.add_parser("restore")
    restore.add_argument("backup_id")
    restore.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.command == "create":
        result = create_backup()
    elif args.command == "list":
        result = {"backups": list_backups()}
    elif args.command == "verify":
        result = verify_backup(args.archive)
    elif args.command == "prune":
        result = {"removed": prune_backups(args.keep)}
    else:
        result = restore_backup(backup_path(args.backup_id), confirmation=args.confirm)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
