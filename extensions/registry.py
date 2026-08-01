"""Filesystem-backed extension registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .manifest import ExtensionManifest, ExtensionValidationError


class ExtensionRegistry:
    def __init__(self, roots: Iterable[str | Path] = ("extensions", "plugins")):
        self.roots = tuple(Path(root) for root in roots)

    def discover(self) -> list[ExtensionManifest]:
        manifests: list[ExtensionManifest] = []
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/extension.json")):
                manifests.append(self.load(path))
        return manifests

    @staticmethod
    def load(path: str | Path) -> ExtensionManifest:
        manifest_path = Path(path)
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return ExtensionManifest.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError, ExtensionValidationError) as exc:
            raise ExtensionValidationError(f"invalid extension manifest: {manifest_path}: {exc}") from exc

    def validate_all(self) -> tuple[list[str], list[ExtensionManifest]]:
        errors: list[str] = []
        valid: list[ExtensionManifest] = []
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/extension.json")):
                try:
                    valid.append(self.load(path))
                except ExtensionValidationError as exc:
                    errors.append(str(exc))
        ids = [item.extension_id for item in valid]
        if len(ids) != len(set(ids)):
            errors.append("duplicate extension_id")
        return errors, valid
