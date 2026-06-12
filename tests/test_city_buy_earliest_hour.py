"""Tests for per-city buy earliest hour configuration."""

import json
from datetime import date, datetime
from unittest.mock import patch

import pytest

from config.settings import (
    RuntimeSettings,
    default_runtime_dict,
    get_city_buy_earliest_hour,
    load_city_buy_earliest_hours,
)
import config.constants as C


def make_settings(**overrides) -> RuntimeSettings:
    data = default_runtime_dict()
    data.update(overrides)
    return RuntimeSettings.from_dict(data)


# ── load_city_buy_earliest_hours ────────────────────────────────────────────


def test_load_city_hours_returns_correct_values(tmp_path):
    """JSON file with valid city/hour pairs loads correctly."""
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(
        json.dumps({"paris": 16, "tokyo": 14, "_comment": "ignored"}),
        encoding="utf-8",
    )
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f):
        result = load_city_buy_earliest_hours()
    assert result == {"paris": 16, "tokyo": 14}


def test_load_city_hours_skips_underscore_keys(tmp_path):
    """Keys starting with '_' (metadata comments) are silently ignored."""
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(
        json.dumps({"_comment": "metadata", "_groups": {}, "london": 16}),
        encoding="utf-8",
    )
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f):
        result = load_city_buy_earliest_hours()
    assert "_comment" not in result
    assert "_groups" not in result
    assert result["london"] == 16


def test_load_city_hours_missing_file_returns_empty(tmp_path):
    """Missing file returns an empty dict without raising."""
    missing = tmp_path / "no_such_file.json"
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", missing):
        result = load_city_buy_earliest_hours()
    assert result == {}


def test_load_city_hours_clamps_to_valid_range(tmp_path):
    """Hours outside 0-23.99 are clamped."""
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(json.dumps({"early_city": -5, "late_city": 99}), encoding="utf-8")
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f):
        result = load_city_buy_earliest_hours()
    assert result["early_city"] == 0.0
    assert result["late_city"] == 23.99


def test_load_city_hours_fractional_value(tmp_path):
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(json.dumps({"tel aviv": 15.5}), encoding="utf-8")
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f):
        result = load_city_buy_earliest_hours()
    assert result["tel aviv"] == pytest.approx(15.5)


def test_load_city_hours_ignores_bad_values(tmp_path):
    """Non-integer values are silently skipped."""
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(
        json.dumps({"paris": "sixteen", "berlin": None, "rome": 15}), encoding="utf-8"
    )
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f):
        result = load_city_buy_earliest_hours()
    assert "paris" not in result
    assert "berlin" not in result
    assert result["rome"] == 15


# ── get_city_buy_earliest_hour ────────────────────────────────────────────


def test_city_lookup_returns_per_city_value(tmp_path):
    """City found in file returns its specific value."""
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(json.dumps({"paris": 16, "tokyo": 14}), encoding="utf-8")
    settings = make_settings(buy_earliest_local_hour=15)
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f):
        assert get_city_buy_earliest_hour("paris", settings) == 16
        assert get_city_buy_earliest_hour("tokyo", settings) == 14


def test_city_lookup_falls_back_to_global(tmp_path):
    """City NOT in file returns the global buy_earliest_local_hour."""
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(json.dumps({"paris": 16}), encoding="utf-8")
    settings = make_settings(buy_earliest_local_hour=15)
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f):
        assert get_city_buy_earliest_hour("berlin", settings) == 15
        assert get_city_buy_earliest_hour("", settings) == 15


def test_city_lookup_is_case_insensitive(tmp_path):
    """Lookup is case-insensitive."""
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(json.dumps({"new york city": 14}), encoding="utf-8")
    settings = make_settings(buy_earliest_local_hour=16)
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f):
        assert get_city_buy_earliest_hour("New York City", settings) == 14
        assert get_city_buy_earliest_hour("NEW YORK CITY", settings) == 14


def test_city_lookup_missing_file_falls_back(tmp_path):
    """When file is missing, falls back to global setting."""
    missing = tmp_path / "no_such_file.json"
    settings = make_settings(buy_earliest_local_hour=16)
    with patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", missing):
        assert get_city_buy_earliest_hour("paris", settings) == 16


# ── production file sanity check ──────────────────────────────────────────


def test_production_city_file_europe_mostly_16():
    hours = load_city_buy_earliest_hours()
    european = [
        "amsterdam",
        "helsinki",
        "london",
        "milan",
        "moscow",
        "munich",
        "paris",
        "warsaw",
    ]
    for city in european:
        assert hours.get(city) == 16.25, f"{city} should be 16.25, got {hours.get(city)}"
    assert hours.get("madrid") == 17.25


def test_production_city_file_most_cities_are_15():
    hours = load_city_buy_earliest_hours()
    for city in ["beijing", "tokyo", "new york city", "chicago"]:
        assert hours.get(city) == 15.25, f"{city} should be 15.25, got {hours.get(city)}"


def test_runtime_settings_preserves_fractional_earliest():
    settings = make_settings(buy_earliest_local_hour=15.5)
    assert settings.buy_earliest_local_hour == pytest.approx(15.5)


# ── integration: time gate uses per-city earliest hour ────────────────────


def test_time_gate_uses_per_city_hour_paris_blocked_at_15(tmp_path):
    """Paris at hour=15 is blocked because Paris requires hour>=16."""
    from strategy.time_filter import entry_time_allowed

    title = "Will the highest temperature in Paris be 22°C on May 13?"
    ev = date(2026, 5, 13)
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(json.dumps({"paris": 16}), encoding="utf-8")
    settings = make_settings(buy_earliest_local_hour=14)

    with (
        patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f),
        patch(
            "strategy.time_filter.city_local_now",
            return_value=datetime(2026, 5, 13, 15, 0),
        ),
    ):
        from config.settings import get_city_buy_earliest_hour

        earliest = get_city_buy_earliest_hour("paris", settings)
        ok, hour, reason = entry_time_allowed(
            title, earliest_hour=earliest, event_date=ev
        )

    assert not ok
    assert reason == "before_earliest_hour"
    assert hour == 15.0


def test_time_gate_uses_per_city_hour_tokyo_allowed_at_14(tmp_path):
    """Tokyo at hour=14 is allowed because Tokyo requires hour>=14."""
    from strategy.time_filter import entry_time_allowed

    title = "Will the highest temperature in Tokyo be 28°C on May 13?"
    ev = date(2026, 5, 13)
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(json.dumps({"tokyo": 14}), encoding="utf-8")
    settings = make_settings(buy_earliest_local_hour=16)

    with (
        patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f),
        patch(
            "strategy.time_filter.city_local_now",
            return_value=datetime(2026, 5, 13, 14, 0),
        ),
    ):
        from config.settings import get_city_buy_earliest_hour

        earliest = get_city_buy_earliest_hour("tokyo", settings)
        ok, hour, reason = entry_time_allowed(
            title, earliest_hour=earliest, event_date=ev
        )

    assert ok
    assert hour == 14.0


def test_time_gate_fractional_earliest_1530(tmp_path):
    from strategy.time_filter import entry_time_allowed

    title = "Will the highest temperature in Tel Aviv be 30°C on May 13?"
    ev = date(2026, 5, 13)
    f = tmp_path / "city_buy_earliest_hour.json"
    f.write_text(json.dumps({"tel aviv": 15.5}), encoding="utf-8")
    settings = make_settings(buy_earliest_local_hour=15.0)

    at_1529 = datetime(2026, 5, 13, 15, 29)
    with (
        patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f),
        patch("strategy.time_filter.city_local_now", return_value=at_1529),
    ):
        earliest = get_city_buy_earliest_hour("tel aviv", settings)
        ok, hour, reason = entry_time_allowed(
            title, earliest_hour=earliest, event_date=ev
        )
    assert not ok and reason == "before_earliest_hour"

    at_1530 = datetime(2026, 5, 13, 15, 30)
    with (
        patch.object(C, "CITY_BUY_EARLIEST_HOURS_FILE", f),
        patch("strategy.time_filter.city_local_now", return_value=at_1530),
    ):
        earliest = get_city_buy_earliest_hour("tel aviv", settings)
        ok, hour, reason = entry_time_allowed(
            title, earliest_hour=earliest, event_date=ev
        )
    assert ok and hour == pytest.approx(15.5)
