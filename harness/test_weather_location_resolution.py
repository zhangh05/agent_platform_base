"""Weather geocoding must not silently choose same-named places elsewhere."""


def test_prd_city_prefers_guangdong_match():
    from core.tools.general_tools.shared_web import (
        _known_weather_place,
        _select_weather_place,
        _weather_geocoding_query,
    )

    matches = [
        {"name": "珠海", "admin1": "山东", "country": "中国"},
        {"name": "珠海", "admin1": "广东", "country": "中国"},
    ]

    selected = _select_weather_place("珠海", matches)

    assert selected["admin1"] == "广东"
    assert _weather_geocoding_query("珠海") == "珠海市"
    assert _weather_geocoding_query("珠海，中国") == "珠海市"
    known = _known_weather_place("广东省珠海市，中国")
    assert known["name"] == "珠海市"
    assert known["admin1"] == "广东"
    assert _known_weather_place("香港，中国")["longitude"] == 114.1694
    assert _known_weather_place("澳门特别行政区")["admin1"] == "澳门特别行政区"


def test_explicit_province_wins_for_unknown_city_name():
    from core.tools.general_tools.shared_web import _select_weather_place

    matches = [
        {"name": "示例", "admin1": "甲省", "country": "中国"},
        {"name": "示例", "admin1": "乙省", "country": "中国"},
    ]

    selected = _select_weather_place("乙省 示例市", matches)

    assert selected["admin1"] == "乙省"


def test_chinese_city_prefers_china_and_population_over_foreign_namesake():
    from core.tools.general_tools.shared_web import (
        _known_weather_place,
        _select_weather_place,
    )

    matches = [
        {
            "name": "Shanghai", "admin1": "Alabama", "country": "United States",
            "country_code": "US", "feature_code": "PPL", "population": 500,
        },
        {
            "name": "Shanghai", "admin1": "上海市", "country": "中国",
            "country_code": "CN", "feature_code": "PPLA", "population": 24_000_000,
        },
    ]

    selected = _select_weather_place("上海", matches)

    assert selected["country_code"] == "CN"
    assert _known_weather_place("上海") == {
        "name": "上海市",
        "admin1": "上海市",
        "country": "中国",
        "latitude": 31.2304,
        "longitude": 121.4737,
    }
    assert _known_weather_place("Beijing, China")["name"] == "北京市"
    assert _known_weather_place("Shanghai")["longitude"] == 121.4737


def test_unknown_same_named_city_prefers_major_administrative_place():
    from core.tools.general_tools.shared_web import _select_weather_place

    matches = [
        {"name": "示例", "country_code": "CN", "feature_code": "PPL", "population": 300},
        {"name": "示例", "country_code": "CN", "feature_code": "PPLA", "population": 800_000},
    ]

    assert _select_weather_place("示例", matches)["feature_code"] == "PPLA"
