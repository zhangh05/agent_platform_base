"""Platform extension contracts and discovery helpers."""

from .manifest import ExtensionManifest, ExtensionValidationError
from .registry import ExtensionRegistry

__all__ = ["ExtensionManifest", "ExtensionRegistry", "ExtensionValidationError"]
