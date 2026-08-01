"""Stable contract for second-party and third-party platform extensions."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


PLATFORM_EXTENSION_API_VERSION = "1"


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
    frontend_routes: tuple[dict[str, Any], ...] = ()
    entrypoint: str = ""
    min_platform_version: str = ""
    max_platform_version: str = ""
    enabled: bool = True
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
        if self.api_version != PLATFORM_EXTENSION_API_VERSION:
            raise ExtensionValidationError(
                f"unsupported extension api_version: {self.api_version}"
            )
        tool_prefix = f"{self.extension_id}."
        if any(not tool_id.startswith(tool_prefix) for tool_id in self.tools):
            raise ExtensionValidationError(
                f"tool ids must start with {tool_prefix}"
            )
        route_prefix = f"/api/extensions/{self.extension_id}"
        if any(not route.startswith(route_prefix) for route in self.routes):
            raise ExtensionValidationError(
                f"backend routes must start with {route_prefix}"
            )
        page_prefix = f"/extensions/{self.extension_id}"
        for route in self.frontend_routes:
            if not isinstance(route, dict):
                raise ExtensionValidationError("frontend_routes entries must be objects")
            if not str(route.get("path") or "").startswith(page_prefix):
                raise ExtensionValidationError(
                    f"frontend route paths must start with {page_prefix}"
                )
            if not str(route.get("module") or "").strip():
                raise ExtensionValidationError("frontend route module is required")
            if str(route.get("module")) not in self.frontend_modules:
                raise ExtensionValidationError("frontend route module must be declared in frontend_modules")
        for module in self.frontend_modules:
            path = str(module)
            if path.startswith("/") or ".." in path.split("/"):
                raise ExtensionValidationError("frontend module must stay inside the extension directory")
        if self.entrypoint:
            module, separator, function = self.entrypoint.partition(":")
            if not separator or not module.endswith(".py") or not function.isidentifier():
                raise ExtensionValidationError("entrypoint must use relative_file.py:function")
            if module.startswith("/") or ".." in module.split("/"):
                raise ExtensionValidationError("entrypoint must stay inside the extension directory")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExtensionManifest":
        known = {field for field in cls.__dataclass_fields__}
        values = {key: value for key, value in data.items() if key in known}
        for key in ("capabilities", "tools", "permissions", "routes", "frontend_modules", "frontend_routes"):
            values[key] = tuple(values.get(key) or ())
        return cls(**values).validate()
