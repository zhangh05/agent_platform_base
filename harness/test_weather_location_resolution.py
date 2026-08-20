"""Generic location resolution must be extensible and ambiguity-safe."""

from __future__ import annotations

from core.resolution.location_models import LocationCandidate
from core.resolution.location_providers import (
    NominatimLocationProvider,
    PhotonLocationProvider,
    _open_meteo_get,
)
from core.resolution.location_service import LocationResolver


def _candidate(
    name: str,
    *,
    provider: str,
    latitude: float,
    longitude: float,
    admin1: str = "",
    country_code: str = "XX",
    place_type: str = "city",
    population: int = 500_000,
) -> LocationCandidate:
    return LocationCandidate(
        canonical_name=name,
        locality=name,
        latitude=latitude,
        longitude=longitude,
        provider=provider,
        admin1=admin1,
        country_code=country_code,
        place_type=place_type,
        population=population,
    )


class FakeProvider:
    supports_reverse = True
    def __init__(self, name: str, *, searches=None, reverse=None, error: Exception | None = None):
        self.name = name
        self.searches = searches or {}
        self.reverse_result = reverse or []
        self.error = error
        self.calls = 0

    def search(self, query, *, language, limit, country_code="", admin_hint=""):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.searches.get(query, []))[:limit]

    def reverse(self, latitude, longitude, *, language):
        if self.error:
            raise self.error
        return list(self.reverse_result)


def test_explicit_admin_hint_resolves_same_named_candidates_without_catalogue():
    provider = FakeProvider("synthetic", searches={
        "示例城": [
            _candidate("示例城", provider="synthetic", latitude=10, longitude=10, admin1="甲省"),
            _candidate("示例城", provider="synthetic", latitude=20, longitude=20, admin1="乙省"),
        ],
    })
    result = LocationResolver((provider,)).resolve("示例城", admin_hint="乙省")

    assert result.ok is True
    assert result.resolved is not None
    assert result.resolved.admin1 == "乙省"
    assert result.provider_chain == ("synthetic",)


def test_weak_primary_candidate_triggers_independent_provider_fallback():
    weak = FakeProvider("weak", searches={
        "新地点": [
            _candidate(
                "新地点", provider="weak", latitude=1, longitude=1,
                place_type="village", population=300,
            ),
        ],
    })
    strong = FakeProvider("strong", searches={
        "新地点": [
            _candidate(
                "新地点", provider="strong", latitude=2, longitude=2,
                place_type="administrative", population=800_000,
            ),
        ],
    })
    unnecessary = FakeProvider("unnecessary", error=AssertionError("must stop"))
    result = LocationResolver((weak, strong, unnecessary)).resolve("新地点")

    assert result.ok is True
    assert result.resolved is not None
    assert result.resolved.provider == "strong"
    assert result.provider_chain == ("weak", "strong")
    assert unnecessary.calls == 0


def test_unresolved_namesake_is_returned_as_ambiguity_not_a_guess():
    provider = FakeProvider("synthetic", searches={
        "Twin": [
            _candidate("Twin", provider="synthetic", latitude=1, longitude=1, admin1="North"),
            _candidate("Twin", provider="synthetic", latitude=2, longitude=2, admin1="South"),
        ],
    })
    result = LocationResolver((provider,)).resolve("Twin")

    assert result.ok is False
    assert result.status == "location_ambiguous"
    assert result.resolved is None
    assert len(result.candidates) == 2


def test_administrative_city_can_outrank_small_non_admin_namesakes():
    provider = FakeProvider("synthetic", searches={
        "Example": [
            _candidate(
                "Example", provider="synthetic", latitude=1, longitude=1,
                admin1="Main", place_type="city", population=0,
            ),
            _candidate(
                "Example", provider="synthetic", latitude=2, longitude=2,
                admin1="Elsewhere", place_type="village", population=10_000,
            ),
        ],
    })

    result = LocationResolver((provider,)).resolve("Example")

    assert result.ok is True
    assert result.resolved is not None
    assert result.resolved.admin1 == "Main"


def test_provider_failure_is_visible_while_later_provider_can_resolve():
    broken = FakeProvider("broken", error=TimeoutError("down"))
    healthy = FakeProvider("healthy", searches={
        "Fallback": [
            _candidate("Fallback", provider="healthy", latitude=3, longitude=4),
        ],
    })
    result = LocationResolver((broken, healthy)).resolve("Fallback")

    assert result.ok is True
    assert result.resolved is not None
    assert result.resolved.provider == "healthy"
    assert result.warnings == ("broken_error:TimeoutError",)


def test_resolver_cache_avoids_repeating_provider_requests():
    provider = FakeProvider("cached", searches={
        "Stable": [_candidate("Stable", provider="cached", latitude=5, longitude=6)],
    })
    resolver = LocationResolver((provider,))

    assert resolver.resolve("Stable").ok is True
    assert resolver.resolve("Stable").ok is True
    assert provider.calls == 1


def test_transient_provider_failure_is_not_cached():
    candidate = _candidate("Retry", provider="flaky", latitude=5, longitude=6)

    class FlakyProvider(FakeProvider):
        def search(self, query, *, language, limit, country_code="", admin_hint=""):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("temporary")
            return [candidate]

    provider = FlakyProvider("flaky")
    resolver = LocationResolver((provider,))

    first = resolver.resolve("Retry")
    second = resolver.resolve("Retry")

    assert first.ok is False
    assert first.warnings == ("flaky_error:TimeoutError",)
    assert second.ok is True
    assert provider.calls == 2


def test_reverse_resolution_uses_first_provider_with_evidence():
    empty = FakeProvider("empty")
    candidate = _candidate("Reverse Place", provider="reverse", latitude=7, longitude=8)
    reverse = FakeProvider("reverse", reverse=[candidate])

    result = LocationResolver((empty, reverse)).reverse(7, 8)

    assert result.ok is True
    assert result.resolved == candidate
    assert result.provider_chain == ("empty", "reverse")


def test_explicit_country_constraint_cannot_be_overridden_by_ranking():
    provider = FakeProvider("synthetic", searches={
        "Only Wrong Country": [
            _candidate(
                "Only Wrong Country", provider="synthetic", latitude=9, longitude=9,
                country_code="AA", population=2_000_000,
            ),
        ],
    })

    result = LocationResolver((provider,)).resolve("Only Wrong Country", country_code="BB")

    assert result.ok is False
    assert result.status == "location_ambiguous"


def test_reverse_skips_provider_that_does_not_declare_support():
    unsupported = FakeProvider("unsupported", reverse=[
        _candidate("Wrong", provider="unsupported", latitude=7, longitude=8),
    ])
    unsupported.supports_reverse = False
    expected = _candidate("Right", provider="reverse", latitude=7, longitude=8)
    reverse = FakeProvider("reverse", reverse=[expected])

    result = LocationResolver((unsupported, reverse)).reverse(7, 8)

    assert result.ok is True
    assert result.resolved == expected
    assert result.provider_chain == ("reverse",)


def test_location_source_contains_no_embedded_city_catalogue():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "core" / "resolution").glob("*.py")
    )
    assert "北京" not in source
    assert "上海" not in source


class _Response:
    def __init__(self, status_code: int, *, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_open_meteo_transport_retries_transient_http_status(monkeypatch):
    import requests

    responses = iter([_Response(429), _Response(200, payload={"results": []})])
    calls = []
    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: calls.append(1) or next(responses))
    monkeypatch.setattr("core.resolution.location_providers.time.sleep", lambda _seconds: None)

    result = _open_meteo_get(params={"name": "Synthetic"})

    assert result.status_code == 200
    assert len(calls) == 2


def test_nominatim_transport_retries_connection_failure(monkeypatch):
    import requests

    responses = iter([
        requests.ConnectionError("temporary"),
        _Response(200, payload=[]),
    ])
    calls = []

    def fake_get(*_args, **_kwargs):
        calls.append(1)
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("core.resolution.location_providers.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(NominatimLocationProvider, "_last_request", 0.0)

    result = NominatimLocationProvider._get("https://example.invalid", params={})

    assert result.status_code == 200
    assert len(calls) == 2


def test_photon_provider_maps_geojson_and_applies_country_constraint(monkeypatch):
    response = _Response(200, payload={
        "features": [{
            "geometry": {"coordinates": [12.5, 34.5]},
            "properties": {
                "name": "Example City",
                "city": "Example City",
                "state": "Example State",
                "country": "Example Country",
                "countrycode": "EX",
                "type": "city",
                "osm_id": 123,
            },
        }, {
            "geometry": {"coordinates": [99, 88]},
            "properties": {"name": "Wrong Country", "countrycode": "NO"},
        }],
    })
    monkeypatch.setattr(
        "core.resolution.location_providers._photon_get",
        lambda **_kwargs: response,
    )

    candidates = PhotonLocationProvider().search(
        "Example City", language="en", limit=5, country_code="EX",
    )

    assert len(candidates) == 1
    assert candidates[0].provider == "photon"
    assert candidates[0].admin1 == "Example State"
    assert candidates[0].latitude == 34.5
    assert candidates[0].longitude == 12.5
