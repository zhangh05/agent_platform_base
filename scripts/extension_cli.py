#!/usr/bin/env python3
"""Create, validate, package, publish, install, and remove platform extensions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from extensions.package import build_package, install_package, uninstall_extension, verify_package
from extensions.registry import ExtensionRegistry
from extensions.repository import list_packages, publish_package
from scripts.create_extension import create_extension


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="create an extension skeleton")
    create.add_argument("extension_id")
    create.add_argument("--name", required=True)
    create.add_argument("--output", default="plugins")

    validate = commands.add_parser("validate", help="validate an extension directory")
    validate.add_argument("source")

    pack = commands.add_parser("pack", help="build a signed .apx package")
    pack.add_argument("source")
    pack.add_argument("--output", required=True)
    pack.add_argument("--private-key", dest="key")

    verify = commands.add_parser("verify", help="verify a signed .apx package")
    verify.add_argument("package")
    verify.add_argument("--public-key", dest="key")

    publish = commands.add_parser("publish", help="publish a verified package to the private repository")
    publish.add_argument("package")
    publish.add_argument("--public-key", dest="key")

    commands.add_parser("list", help="list packages in the private repository")

    install = commands.add_parser("install", help="install or upgrade a signed package")
    install.add_argument("package")
    install.add_argument("--public-key", dest="key")
    install.add_argument("--plugins-root")
    install.add_argument("--upgrade", action="store_true")

    remove = commands.add_parser("uninstall", help="move a plugin to the recoverable trash")
    remove.add_argument("extension_id")
    remove.add_argument("--plugins-root")

    args = parser.parse_args()
    if args.command == "create":
        _print({"ok": True, "path": str(create_extension(args.extension_id, args.name, Path(args.output)))})
    elif args.command == "validate":
        manifest = ExtensionRegistry.load(Path(args.source) / "extension.json")
        _print({"ok": True, "manifest": manifest.to_dict()})
    elif args.command == "pack":
        _print(build_package(args.source, args.output, key=args.key))
    elif args.command == "verify":
        _print(verify_package(args.package, key=args.key))
    elif args.command == "publish":
        _print(publish_package(args.package, key=args.key))
    elif args.command == "list":
        _print({"ok": True, "packages": list_packages()})
    elif args.command == "install":
        _print(install_package(args.package, key=args.key, plugins_root=args.plugins_root, upgrade=args.upgrade))
    elif args.command == "uninstall":
        _print(uninstall_extension(args.extension_id, plugins_root=args.plugins_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
