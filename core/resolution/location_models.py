"""Provider-neutral schemas for geographic entity resolution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class LocationCandidate:
    canonical_name: str
    latitude: float
    longitude: float
    provider: str
    provider_id: str = ""
    country: str = ""
    country_code: str = ""
    admin1: str = ""
    admin2: str = ""
    locality: str = ""
    place_type: str = "unknown"
    population: int = 0
    importance: float = 0.0
    timezone: str = ""
    corroborating_providers: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


@dataclass(frozen=True)
class LocationResolution:
    ok: bool
    query: str
    status: str
    resolved: LocationCandidate | None = None
    candidates: tuple[LocationCandidate, ...] = ()
    confidence: float = 0.0
    provider_chain: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "query": self.query,
            "status": self.status,
            "resolved": self.resolved.public_dict() if self.resolved else None,
            "candidates": [item.public_dict() for item in self.candidates],
            "confidence": round(self.confidence, 3),
            "provider_chain": list(self.provider_chain),
            "warnings": list(self.warnings),
        }
