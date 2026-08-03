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


def test_explicit_province_wins_for_unknown_city_name():
    from core.tools.general_tools.shared_web import _select_weather_place

    matches = [
        {"name": "示例", "admin1": "甲省", "country": "中国"},
        {"name": "示例", "admin1": "乙省", "country": "中国"},
    ]

    selected = _select_weather_place("乙省 示例市", matches)

    assert selected["admin1"] == "乙省"
