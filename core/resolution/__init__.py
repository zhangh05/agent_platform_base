"""Reusable entity-resolution capabilities for LZCore."""

from .location_service import (
    LocationResolver,
    resolve_location,
    resolve_locations,
    reverse_location,
)

__all__ = ["LocationResolver", "resolve_location", "resolve_locations", "reverse_location"]
