"""Polymarket US daily max markets use °F in titles; we normalize to °C for Open-Meteo."""

from forecast.parse_title import BracketKind, parse_highest_temp_title


def test_chicago_at_least_f() -> None:
    p = parse_highest_temp_title(
        "Will the highest temperature in Chicago be 46°F or higher on April 11?"
    )
    assert p is not None
    assert p.city_key == "chicago"
    assert p.bracket == BracketKind.AT_LEAST
    assert abs(p.threshold_c - 7.778) < 0.02


def test_nyc_range_f_midpoint_c() -> None:
    p = parse_highest_temp_title(
        "Will the highest temperature in NYC be 62-63°F on April 11?"
    )
    assert p is not None
    assert p.city_key == "nyc"
    assert p.bracket == BracketKind.EXACT
    assert 16.5 < p.threshold_c < 17.5


def test_atlanta_between_range_f() -> None:
    p = parse_highest_temp_title(
        "Will the highest temperature in Atlanta be between 86-87°F on April 15?"
    )
    assert p is not None
    assert p.city_key == "atlanta"
    assert p.bracket == BracketKind.EXACT
    assert 29.9 < p.threshold_c < 30.7


def test_seattle_below_f() -> None:
    p = parse_highest_temp_title(
        "Will the highest temperature in Seattle be 53°F or below on April 11?"
    )
    assert p is not None
    assert p.city_key == "seattle"
    assert p.bracket == BracketKind.AT_MOST


def main() -> None:
    test_chicago_at_least_f()
    test_nyc_range_f_midpoint_c()
    test_atlanta_between_range_f()
    test_seattle_below_f()
    print("ok: fahrenheit title parse")


if __name__ == "__main__":
    main()
