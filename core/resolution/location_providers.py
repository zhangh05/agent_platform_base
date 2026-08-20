"""Geocoding provider adapters behind one stable resolver interface."""

from __future__ import annotations

import re
import threading
import time
from typing import Protocol

from .location_models import LocationCandidate

_USER_AGENT = "LZCore/2.3 (+https://github.com/zhangh05/lzcore)"
_ADMIN_SUFFIXES = "省市区县州盟旗特别行政自治区"
_EXPLICIT_ADMIN_SUFFIXES = ("省", "市", "区", "县", "盟", "旗", "特别行政区", "自治区", "自治州")
_RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_OPEN_METEO_SLOTS = threading.BoundedSemaphore(3)
_PHOTON_SLOTS = threading.BoundedSemaphore(3)


class LocationProviderUnavailable(RuntimeError):
    """Raised when a provider exhausted transient-failure recovery."""


def _retry_delay(response: object | None, attempt: int) -> float:
    headers = getattr(response, "headers", {}) or {}
    try:
        retry_after = float(headers.get("Retry-After") or 0)
    except (TypeError, ValueError):
        retry_after = 0
    return max(retry_after, 0.4 * (2 ** attempt))


def _provider_get(
    url: str,
    *,
    params: dict,
    slots: threading.BoundedSemaphore,
    provider_name: str,
) -> object:
    """Bound and retry an idempotent geocoding-provider request."""
    import requests

    last_error: Exception | None = None
    last_response: object | None = None
    with slots:
        for attempt in range(3):
            try:
                response = requests.get(
                    url,
                    params=params,
                    timeout=15,
                    headers={"User-Agent": _USER_AGENT},
                )
                last_response = response
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(_retry_delay(None, attempt))
                continue
            if response.status_code == 200:
                return response
            if response.status_code not in _RETRYABLE_HTTP_STATUSES:
                return response
            if attempt < 2:
                time.sleep(_retry_delay(response, attempt))
    detail = (
        type(last_error).__name__
        if last_error else getattr(last_response, "status_code", "unknown")
    )
    raise LocationProviderUnavailable(
        f"{provider_name} unavailable after retries: {detail}",
    )


def _open_meteo_get(*, params: dict) -> object:
    return _provider_get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params=params,
        slots=_OPEN_METEO_SLOTS,
        provider_name="open_meteo",
    )


def _photon_get(*, params: dict) -> object:
    return _provider_get(
        "https://photon.komoot.io/api/",
        params=params,
        slots=_PHOTON_SLOTS,
        provider_name="photon",
    )


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
        and not first_component.endswith(_EXPLICIT_ADMIN_SUFFIXES)
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
                response = _open_meteo_get(params=params)
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


class PhotonLocationProvider:
    """Keyless OSM-backed forward geocoder used as an independent fallback."""

    name = "photon"
    supports_reverse = False

    def search(
        self, query: str, *, language: str, limit: int,
        country_code: str = "", admin_hint: str = "",
    ) -> list[LocationCandidate]:
        params: dict[str, object] = {
            "q": ", ".join(item for item in (query, admin_hint) if item),
            "limit": max(1, min(limit * 2, 20)),
        }
        language_code = language.split("-", 1)[0].lower()
        if language_code in {"de", "en", "fr"}:
            params["lang"] = language_code
        response = _photon_get(params=params)
        if response.status_code != 200:
            return []
        found: list[LocationCandidate] = []
        for feature in response.json().get("features") or []:
            properties = feature.get("properties") or {}
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            try:
                longitude = float(coordinates[0])
                latitude = float(coordinates[1])
            except (IndexError, TypeError, ValueError):
                continue
            candidate_country = str(properties.get("countrycode") or "").upper()
            if country_code and candidate_country != country_code.upper():
                continue
            canonical_name = str(properties.get("name") or properties.get("city") or query)
            found.append(LocationCandidate(
                canonical_name=canonical_name,
                latitude=latitude,
                longitude=longitude,
                provider=self.name,
                provider_id=str(properties.get("osm_id") or ""),
                country=str(properties.get("country") or ""),
                country_code=candidate_country,
                admin1=str(properties.get("state") or ""),
                admin2=str(properties.get("county") or ""),
                locality=str(properties.get("city") or canonical_name),
                place_type=str(properties.get("type") or "unknown"),
                raw=feature,
            ))
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
        # all fallback, reverse, and retry calls so a batch remains compliant.
        with cls._lock:
            last_error: Exception | None = None
            last_response: object | None = None
            for attempt in range(3):
                remaining = 1.05 - (time.monotonic() - cls._last_request)
                if remaining > 0:
                    time.sleep(remaining)
                try:
                    response = requests.get(
                        url, params=params, timeout=20,
                        headers={"User-Agent": _USER_AGENT},
                    )
                    last_response = response
                except requests.RequestException as exc:
                    last_error = exc
                    cls._last_request = time.monotonic()
                    if attempt == 2:
                        break
                    time.sleep(_retry_delay(None, attempt))
                    continue
                cls._last_request = time.monotonic()
                if response.status_code == 200:
                    return response
                if response.status_code not in _RETRYABLE_HTTP_STATUSES:
                    return response
                if attempt < 2:
                    time.sleep(_retry_delay(response, attempt))
            detail = (
                type(last_error).__name__
                if last_error else getattr(last_response, "status_code", "unknown")
            )
            raise LocationProviderUnavailable(f"nominatim unavailable after retries: {detail}")

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
    "LocationProviderUnavailable",
    "NominatimLocationProvider",
    "OpenMeteoLocationProvider",
    "PhotonLocationProvider",
]
