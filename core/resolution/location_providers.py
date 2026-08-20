"""Geocoding provider adapters behind one stable resolver interface."""

from __future__ import annotations

import re
import threading
import time
from typing import Protocol

from .location_models import LocationCandidate

_USER_AGENT = "LZCore/2.3 (+https://github.com/zhangh05/lzcore)"
_ADMIN_SUFFIXES = "省市区县州盟旗特别行政自治区"


class LocationProvider(Protocol):
    name: str
    supports_reverse: bool

    def search(
        self, query: str, *, language: str, limit: int,
        country_code: str = "", admin_hint: str = "",
    ) -> list[LocationCandidate]: ...

    def reverse(self, latitude: float, longitude: float, *, language: str) -> list[LocationCandidate]: ...


def _query_variants(query: str) -> list[str]:
    """Generate syntax variants without embedding any place catalogue."""
    value = str(query or "").strip()
    variants = [value]
    first_component = re.split(r"[,，、]", value, maxsplit=1)[0].strip()
    if (
        first_component
        and re.search(r"[\u3400-\u9fff]", first_component)
        and not first_component.endswith(tuple(_ADMIN_SUFFIXES))
    ):
        variants.append(f"{first_component}市")
    return list(dict.fromkeys(item for item in variants if item))


def _place_token(value: str) -> str:
    return re.sub(rf"[\s,，、{_ADMIN_SUFFIXES}]+", "", str(value or "")).casefold()


class OpenMeteoLocationProvider:
    name = "open_meteo"
    supports_reverse = False

    def search(
        self, query: str, *, language: str, limit: int,
        country_code: str = "", admin_hint: str = "",
    ) -> list[LocationCandidate]:
        import requests

        found: list[LocationCandidate] = []
        query_languages = [language]
        if re.search(r"[A-Za-z]", f"{query} {admin_hint}") and not language.lower().startswith("en"):
            query_languages.append("en")
        variants = _query_variants(query)
        for variant_index, variant in enumerate(variants):
            for query_language in dict.fromkeys(query_languages):
                params = {
                    "name": variant,
                    # Fetch more than the public candidate display limit so a
                    # major administrative place is not hidden behind several
                    # small namesakes returned first by a fuzzy provider.
                    "count": 20,
                    "language": query_language,
                    "format": "json",
                }
                if country_code:
                    params["countryCode"] = country_code.upper()
                response = requests.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params=params,
                    timeout=15,
                    headers={"User-Agent": _USER_AGENT},
                )
                if response.status_code != 200:
                    continue
                for raw in response.json().get("results") or []:
                    try:
                        latitude = float(raw["latitude"])
                        longitude = float(raw["longitude"])
                        population = max(0, int(raw.get("population") or 0))
                    except (KeyError, TypeError, ValueError):
                        continue
                    feature = str(raw.get("feature_code") or "").upper()
                    found.append(LocationCandidate(
                        canonical_name=str(raw.get("name") or variant),
                        latitude=latitude,
                        longitude=longitude,
                        provider=self.name,
                        provider_id=str(raw.get("id") or ""),
                        country=str(raw.get("country") or ""),
                        country_code=str(raw.get("country_code") or "").upper(),
                        admin1=str(raw.get("admin1") or ""),
                        admin2=str(raw.get("admin2") or ""),
                        locality=str(raw.get("name") or ""),
                        place_type=feature or "unknown",
                        population=population,
                        timezone=str(raw.get("timezone") or ""),
                        raw=raw,
                    ))
            if variant_index == 0 and any(
                _place_token(item.canonical_name) == _place_token(query)
                and (item.place_type in {"PPLC", "PPLA", "PPLA2"}
                     or item.population >= 50_000)
                for item in found
            ):
                break
        return found

    def reverse(self, latitude: float, longitude: float, *, language: str) -> list[LocationCandidate]:
        return []


class NominatimLocationProvider:
    name = "nominatim"
    supports_reverse = True
    _lock = threading.Lock()
    _last_request = 0.0

    @classmethod
    def _get(cls, url: str, *, params: dict) -> object:
        import requests

        # The public endpoint requires a single request per second. Serialise
        # all fallback and reverse calls so a batch remains policy-compliant.
        with cls._lock:
            remaining = 1.05 - (time.monotonic() - cls._last_request)
            if remaining > 0:
                time.sleep(remaining)
            response = requests.get(
                url, params=params, timeout=20,
                headers={"User-Agent": _USER_AGENT},
            )
            cls._last_request = time.monotonic()
        return response

    @staticmethod
    def _candidate(raw: dict) -> LocationCandidate | None:
        address = raw.get("address") or {}
        try:
            latitude = float(raw["lat"])
            longitude = float(raw["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        place_type = str(raw.get("type") or "unknown")
        display_name = str(raw.get("display_name") or "")
        canonical_name = str(raw.get("name") or display_name.split(",", 1)[0])
        return LocationCandidate(
            canonical_name=canonical_name,
            latitude=latitude,
            longitude=longitude,
            provider="nominatim",
            provider_id=str(raw.get("place_id") or raw.get("osm_id") or ""),
            country=str(address.get("country") or ""),
            country_code=str(address.get("country_code") or "").upper(),
            admin1=str(address.get("state") or address.get("province") or address.get("region") or ""),
            admin2=str(address.get("county") or address.get("state_district") or ""),
            locality=str(
                address.get("city") or address.get("town") or address.get("municipality")
                or address.get("village") or canonical_name
            ),
            place_type=place_type,
            importance=max(0.0, float(raw.get("importance") or 0.0)),
            raw=raw,
        )

    def search(
        self, query: str, *, language: str, limit: int,
        country_code: str = "", admin_hint: str = "",
    ) -> list[LocationCandidate]:
        qualified_query = ", ".join(
            item for item in (query, admin_hint, country_code.upper()) if str(item or "").strip()
        )
        response = self._get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": qualified_query,
                "format": "jsonv2",
                "limit": max(1, min(limit, 10)),
                "addressdetails": 1,
                "accept-language": language,
            },
        )
        if getattr(response, "status_code", 0) != 200:
            return []
        return [
            candidate for raw in response.json()
            if (candidate := self._candidate(raw)) is not None
        ]

    def reverse(self, latitude: float, longitude: float, *, language: str) -> list[LocationCandidate]:
        response = self._get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": latitude,
                "lon": longitude,
                "format": "jsonv2",
                "addressdetails": 1,
                "accept-language": language,
            },
        )
        if getattr(response, "status_code", 0) != 200:
            return []
        candidate = self._candidate(response.json())
        return [candidate] if candidate else []


__all__ = [
    "LocationProvider",
    "NominatimLocationProvider",
    "OpenMeteoLocationProvider",
]
