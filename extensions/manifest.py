"""Stable contract for second-party and third-party platform extensions."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


class ExtensionValidationError(ValueError):
    """Raised when an extension manifest is unsafe or incomplete."""


@dataclass(frozen=True)
class ExtensionManifest:
    extension_id: str
    name: str
    version: str
    api_version: str = "1"
    description: str = ""
    capabilities: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()
    frontend_modules: tuple[str, ...] = ()
    entrypoint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> "ExtensionManifest":
        if not self.extension_id or not self.extension_id.replace(".", "").replace("-", "").replace("_", "").isalnum():
            raise ExtensionValidationError("extension_id must be a dotted identifier")
        if not self.name.strip() or not self.version.strip():
            raise ExtensionValidationError("name and version are required")
        for label, values in (("capabilities", self.capabilities), ("tools", self.tools), ("permissions", self.permissions)):
            if len(set(values)) != len(values):
                raise ExtensionValidationError(f"{label} contains duplicates")
            if any(not str(value).strip() for value in values):
                raise ExtensionValidationError(f"{label} contains an empty value")
        if not self.tools and not self.capabilities and not self.routes:
            raise ExtensionValidationError("an extension must declare a capability, tool, or route")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtensionManifest":
        known = {field for field in cls.__dataclass_fields__}
        values = {key: value for key, value in data.items() if key in known}
        for key in ("capabilities", "tools", "permissions", "routes", "frontend_modules"):
            values[key] = tuple(values.get(key) or ())
        return cls(**values).validate()
