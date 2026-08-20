"""Generic geographic entity resolution with evidence and ambiguity control."""

from __future__ import annotations

import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .location_models import LocationCandidate, LocationResolution
from .location_providers import (
    LocationProvider,
    NominatimLocationProvider,
    OpenMeteoLocationProvider,
)

_ADMIN_SUFFIXES = "省市区县州盟旗特别行政自治区"
_PROMINENT_TYPES = {
    "PPLC", "PPLA", "PPLA2", "PPLA3", "PPLA4",
    "administrative", "city", "municipality", "town",
}


def _token(value: object) -> str:
    return re.sub(rf"[\s,，、{_ADMIN_SUFFIXES}]+", "", str(value or "")).casefold()


def _score(
    query: str,
    candidate: LocationCandidate,
    *,
    country_code: str,
    admin_hint: str,
) -> float:
    query_token = _token(query)
    names = {
        _token(candidate.canonical_name),
        _token(candidate.locality),
    } - {""}
    score = 0.0
    if query_token in names:
        score += 100
    elif any(name in query_token or query_token in name for name in names):
        score += 70
    candidate_admin = _token(candidate.admin1)
    effective_admin = _token(admin_hint)
    if candidate_admin and candidate_admin in query_token:
        score += 45
    if effective_admin:
        score += 55 if candidate_admin == effective_admin else -35
    requested_country = country_code.strip().upper()
    if requested_country:
        score += 50 if candidate.country_code == requested_country else -80
    score += {
        "PPLC": 35, "PPLA": 33, "PPLA2": 30, "PPLA3": 26,
        "PPLA4": 22, "administrative": 28, "city": 27,
        "municipality": 27, "town": 14, "PPL": 8, "village": 4,
    }.get(candidate.place_type, 0)
    if candidate.population:
        score += min(24.0, math.log10(candidate.population + 1) * 3.5)
    score += min(20.0, candidate.importance * 20)
    return score


def _deduplicate(candidates: list[LocationCandidate]) -> list[LocationCandidate]:
    unique: list[LocationCandidate] = []
    for candidate in candidates:
        match_index = next((
            index for index, current in enumerate(unique)
            if _token(current.canonical_name) == _token(candidate.canonical_name)
            and _token(current.admin1) == _token(candidate.admin1)
            and current.country_code == candidate.country_code
            and _distance_km(current, candidate) <= 50
        ), None)
        if match_index is None:
            unique.append(candidate)
            continue
        current = unique[match_index]
        preferred = max(
            (current, candidate),
            key=lambda item: (
                bool(item.timezone), item.population, item.importance,
                item.place_type in _PROMINENT_TYPES,
            ),
        )
        providers = tuple(dict.fromkeys(
            (*current.corroborating_providers, current.provider,
             *candidate.corroborating_providers, candidate.provider)
        ))
        unique[match_index] = LocationCandidate(
            canonical_name=preferred.canonical_name,
            latitude=preferred.latitude,
            longitude=preferred.longitude,
            provider=preferred.provider,
            provider_id=preferred.provider_id,
            country=preferred.country or current.country or candidate.country,
            country_code=preferred.country_code or current.country_code or candidate.country_code,
            admin1=preferred.admin1 or current.admin1 or candidate.admin1,
            admin2=preferred.admin2 or current.admin2 or candidate.admin2,
            locality=preferred.locality or current.locality or candidate.locality,
            place_type=preferred.place_type,
            population=max(current.population, candidate.population),
            importance=max(current.importance, candidate.importance),
            timezone=preferred.timezone or current.timezone or candidate.timezone,
            corroborating_providers=providers,
            raw=preferred.raw,
        )
    return unique


def _distance_km(left: LocationCandidate, right: LocationCandidate) -> float:
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        (left.latitude, left.longitude, right.latitude, right.longitude),
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(value)))


def _rank(
    query: str,
    candidates: list[LocationCandidate],
    *,
    country_code: str,
    admin_hint: str,
) -> list[LocationCandidate]:
    return sorted(
        _deduplicate(candidates),
        key=lambda item: _score(
            query, item, country_code=country_code, admin_hint=admin_hint,
        ),
        reverse=True,
    )


def _confidence(
    query: str,
    ranked: list[LocationCandidate],
    *,
    country_code: str,
    admin_hint: str,
) -> float:
    if not ranked:
        return 0.0
    best_score = _score(query, ranked[0], country_code=country_code, admin_hint=admin_hint)
    gap = best_score - (
        _score(query, ranked[1], country_code=country_code, admin_hint=admin_hint)
        if len(ranked) > 1 else 0.0
    )
    name_match = any(
        name and (name in _token(query) or _token(query) in name)
        for name in (_token(ranked[0].canonical_name), _token(ranked[0].locality))
    )
    prominent = ranked[0].place_type in _PROMINENT_TYPES or ranked[0].population >= 50_000
    base = 0.58 + min(max(best_score - 90, 0), 60) / 200
    if name_match:
        base += 0.12
    if prominent:
        base += 0.10
    if gap >= 15:
        base += 0.10
    elif gap < 8 and len(ranked) > 1:
        base -= 0.20
    exact_namesakes = {
        (_token(item.admin1), item.country_code, round(item.latitude, 3), round(item.longitude, 3))
        for item in ranked
        if _token(item.canonical_name) == _token(query) or _token(item.locality) == _token(query)
    }
    exact_candidates = [
        item for item in ranked
        if _token(item.canonical_name) == _token(query) or _token(item.locality) == _token(query)
    ]
    top_population = exact_candidates[0].population if exact_candidates else 0
    second_population = max((item.population for item in exact_candidates[1:]), default=0)
    population_dominates = top_population >= 250_000 and top_population >= max(1, second_population) * 5
    if country_code and ranked[0].country_code != country_code.strip().upper():
        return 0.0
    if admin_hint and _token(ranked[0].admin1) != _token(admin_hint):
        base = min(base, 0.70)
    if not prominent:
        base = min(base, 0.72)
    if len(ranked) > 1 and gap < 8 and not population_dominates and not admin_hint:
        base = min(base, 0.70)
    if len(exact_namesakes) > 1 and gap < 15 and not admin_hint and not population_dominates:
        base = min(base, 0.70)
    return max(0.0, min(base, 0.99))


class LocationResolver:
    """Extensible resolver that composes ordered provider adapters."""

    def __init__(self, providers: tuple[LocationProvider, ...] | None = None):
        self.providers = providers or (
            OpenMeteoLocationProvider(), NominatimLocationProvider(),
        )
        self._cache: dict[tuple[object, ...], tuple[float, LocationResolution]] = {}
        self._cache_lock = threading.Lock()

    def resolve(
        self,
        query: str,
        *,
        language: str = "zh",
        country_code: str = "",
        admin_hint: str = "",
        limit: int = 5,
    ) -> LocationResolution:
        query = str(query or "").strip()
        language = str(language or "zh").strip() or "zh"
        country_code = str(country_code or "").strip().upper()
        admin_hint = str(admin_hint or "").strip()
        limit = max(1, min(int(limit or 5), 10))
        if not query:
            return LocationResolution(False, query, "location_required")
        cache_key = (query.casefold(), language.casefold(), country_code, admin_hint.casefold(), limit)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]
            if cached:
                self._cache.pop(cache_key, None)
        resolution = self._resolve_from_providers(
            query, language=language, country_code=country_code,
            admin_hint=admin_hint, limit=limit,
        )
        ttl = self._cache_ttl(resolution)
        if ttl > 0:
            with self._cache_lock:
                if len(self._cache) >= 2048:
                    oldest = min(self._cache, key=lambda key: self._cache[key][0])
                    self._cache.pop(oldest, None)
                self._cache[cache_key] = (time.monotonic() + ttl, resolution)
        return resolution

    @staticmethod
    def _cache_ttl(resolution: LocationResolution) -> float:
        if any("_error:" in warning for warning in resolution.warnings):
            return 0
        if resolution.ok:
            return 86_400
        if resolution.status == "location_ambiguous":
            return 900
        if resolution.status == "location_not_found":
            return 300
        return 0

    def _resolve_from_providers(
        self,
        query: str,
        *,
        language: str,
        country_code: str,
        admin_hint: str,
        limit: int,
    ) -> LocationResolution:
        candidates: list[LocationCandidate] = []
        provider_chain: list[str] = []
        warnings: list[str] = []
        for index, provider in enumerate(self.providers):
            provider_chain.append(provider.name)
            try:
                candidates.extend(provider.search(
                    query, language=language, limit=limit,
                    country_code=country_code, admin_hint=admin_hint,
                ))
            except Exception as exc:  # noqa: BLE001 - isolate an external provider boundary
                warnings.append(f"{provider.name}_error:{type(exc).__name__}")
            ranked = _rank(
                query, candidates, country_code=country_code, admin_hint=admin_hint,
            )
            confidence = _confidence(
                query, ranked, country_code=country_code, admin_hint=admin_hint,
            )
            # A strong primary result avoids a slower fallback. Otherwise
            # collect independent candidates before accepting or rejecting.
            if index == 0 and confidence >= 0.86:
                break
        ranked = _rank(query, candidates, country_code=country_code, admin_hint=admin_hint)
        confidence = _confidence(
            query, ranked, country_code=country_code, admin_hint=admin_hint,
        )
        visible = tuple(ranked[:limit])
        if not ranked:
            return LocationResolution(
                False, query, "location_not_found", candidates=visible,
                provider_chain=tuple(provider_chain), warnings=tuple(warnings),
            )
        if confidence < 0.78:
            return LocationResolution(
                False, query, "location_ambiguous", candidates=visible,
                confidence=confidence, provider_chain=tuple(provider_chain),
                warnings=tuple(warnings),
            )
        return LocationResolution(
            True, query, "resolved", resolved=ranked[0], candidates=visible,
            confidence=confidence, provider_chain=tuple(provider_chain),
            warnings=tuple(warnings),
        )

    def resolve_many(
        self,
        queries: list[str],
        *,
        language: str = "zh",
        country_code: str = "",
        admin_hint: str = "",
        limit: int = 5,
    ) -> list[LocationResolution]:
        cleaned = list(dict.fromkeys(
            str(item or "").strip() for item in queries if str(item or "").strip()
        ))
        with ThreadPoolExecutor(max_workers=min(5, max(1, len(cleaned)))) as pool:
            return list(pool.map(
                lambda item: self.resolve(
                    item, language=language, country_code=country_code,
                    admin_hint=admin_hint, limit=limit,
                ),
                cleaned,
            ))

    def reverse(
        self,
        latitude: float,
        longitude: float,
        *,
        language: str = "zh",
    ) -> LocationResolution:
        query = f"{float(latitude):.6f},{float(longitude):.6f}"
        provider_chain: list[str] = []
        warnings: list[str] = []
        for provider in self.providers:
            if not getattr(provider, "supports_reverse", True):
                continue
            provider_chain.append(provider.name)
            try:
                candidates = provider.reverse(
                    float(latitude), float(longitude), language=language,
                )
            except Exception as exc:  # noqa: BLE001 - isolate an external provider boundary
                warnings.append(f"{provider.name}_error:{type(exc).__name__}")
                continue
            if candidates:
                return LocationResolution(
                    True, query, "resolved", resolved=candidates[0],
                    candidates=tuple(candidates[:3]), confidence=0.95,
                    provider_chain=tuple(provider_chain), warnings=tuple(warnings),
                )
        return LocationResolution(
            False, query, "location_not_found", provider_chain=tuple(provider_chain),
            warnings=tuple(warnings),
        )


DEFAULT_LOCATION_RESOLVER = LocationResolver()


def resolve_location(query: str, **kwargs) -> LocationResolution:
    return DEFAULT_LOCATION_RESOLVER.resolve(query, **kwargs)


def resolve_locations(queries: list[str], **kwargs) -> list[LocationResolution]:
    return DEFAULT_LOCATION_RESOLVER.resolve_many(queries, **kwargs)


def reverse_location(latitude: float, longitude: float, **kwargs) -> LocationResolution:
    return DEFAULT_LOCATION_RESOLVER.reverse(latitude, longitude, **kwargs)


__all__ = [
    "DEFAULT_LOCATION_RESOLVER",
    "LocationResolver",
    "resolve_location",
    "resolve_locations",
    "reverse_location",
]
