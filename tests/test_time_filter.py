"""entry_time_allowed: city local date must match event_date when provided."""

from datetime import date, datetime
from unittest.mock import patch

import pytest

from strategy.time_filter import entry_time_allowed


def test_entry_allowed_same_event_day_afternoon():
    title = "Will the highest temperature in London be 18°C on April 17?"
    ev = date(2026, 4, 17)
    noon = datetime(2026, 4, 17, 14, 0)
    with patch("strategy.time_filter.city_local_now", return_value=noon):
        ok, hour, reason = entry_time_allowed(title, event_date=ev)
    assert ok and hour == 14.0 and reason == ""


def test_entry_blocked_day_before_even_late_hour():
    """22:00 on Apr 16 London must not allow a market whose event_date is Apr 17."""
    title = "Will the highest temperature in London be 18°C on April 17?"
    ev = date(2026, 4, 17)
    evening_prior = datetime(2026, 4, 16, 22, 6)
    with patch("strategy.time_filter.city_local_now", return_value=evening_prior):
        ok, hour, reason = entry_time_allowed(title, event_date=ev)
    assert not ok and hour == pytest.approx(22.1) and reason == "city_local_date_not_event_day"


def test_entry_blocked_event_morning_before_14():
    title = "Will the highest temperature in Dallas be between 86-87°F on April 16?"
    ev = date(2026, 4, 16)
    morning = datetime(2026, 4, 16, 13, 0)
    with patch("strategy.time_filter.city_local_now", return_value=morning):
        ok, hour, reason = entry_time_allowed(title, event_date=ev)
    assert not ok and hour == 13.0 and reason == "before_earliest_hour"


def test_entry_blocked_before_fractional_earliest():
    title = "Will the highest temperature in London be 18°C on April 17?"
    ev = date(2026, 4, 17)
    at_1529 = datetime(2026, 4, 17, 15, 29)
    with patch("strategy.time_filter.city_local_now", return_value=at_1529):
        ok, hour, reason = entry_time_allowed(
            title, earliest_hour=15.5, event_date=ev
        )
    assert not ok and hour == pytest.approx(15 + 29 / 60.0) and reason == "before_earliest_hour"


def test_entry_allowed_at_fractional_earliest():
    title = "Will the highest temperature in London be 18°C on April 17?"
    ev = date(2026, 4, 17)
    at_1530 = datetime(2026, 4, 17, 15, 30)
    with patch("strategy.time_filter.city_local_now", return_value=at_1530):
        ok, hour, reason = entry_time_allowed(
            title, earliest_hour=15.5, event_date=ev
        )
    assert ok and hour == 15.5 and reason == ""


def test_unknown_city_still_permissive_hour_none():
    title = "Will the highest temperature in FictionalCity be 20°C on April 17?"
    ev = date(2026, 4, 17)
    with patch("strategy.time_filter.city_local_now", return_value=None):
        ok, hour, reason = entry_time_allowed(title, event_date=ev)
    assert ok and hour is None and reason == ""
