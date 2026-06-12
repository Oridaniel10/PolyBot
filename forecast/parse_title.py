"""Parse Polymarket highest-temperature question titles: city, event date, temperature bracket."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]

from config import constants as C

from strategy.city_tz import CITY_TZ, _extract_city_from_title


class BracketKind(Enum):
    AT_MOST = "at_most"  # X°C or below
    AT_LEAST = "at_least"  # X°C or above
    EXACT = "exact"  # X°C on <date> (narrow bucket)


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_DATE_TAIL = re.compile(
    r"\bon\s+([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?\s*\?",
    re.IGNORECASE,
)
_RE_BELOW = re.compile(
    r"be\s+(\d+(?:\.\d+)?)\s*°?\s*c\s+or\s+below",
    re.IGNORECASE,
)
_RE_ABOVE = re.compile(
    r"be\s+(\d+(?:\.\d+)?)\s*°?\s*c\s+or\s+above",
    re.IGNORECASE,
)
_RE_EXACT_ON = re.compile(
    r"be\s+(\d+(?:\.\d+)?)\s*°?\s*c\s+on\s",
    re.IGNORECASE,
)
# US Polymarket titles use °F (ranges like 62-63°F, or higher / or below)
_RE_BELOW_F = re.compile(
    r"be\s+(\d+(?:\.\d+)?)\s*°?\s*f\s+or\s+below",
    re.IGNORECASE,
)
_RE_ABOVE_F = re.compile(
    r"be\s+(\d+(?:\.\d+)?)\s*°?\s*f\s+or\s+(?:higher|above)",
    re.IGNORECASE,
)
_RE_RANGE_F = re.compile(
    r"be\s+(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*°?\s*f\b",
    re.IGNORECASE,
)
_RE_BETWEEN_RANGE_F = re.compile(
    r"between\s+(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*°?\s*f\b",
    re.IGNORECASE,
)
_RE_EXACT_F_ON = re.compile(
    r"be\s+(\d+(?:\.\d+)?)\s*°?\s*f\s+on\s",
    re.IGNORECASE,
)


def _fahrenheit_to_celsius(f: float) -> float:
    return (f - 32.0) * (5.0 / 9.0)


@dataclass
class ParsedTempMarket:
    city_key: str
    tz_name: str
    event_date: date
    bracket: BracketKind
    threshold_c: float
    raw_title: str


def _resolve_event_date(
    month: int, day: int, year_opt: Optional[int], tz_name: str
) -> Optional[date]:
    if ZoneInfo is None:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None
    today = datetime.now(tz).date()
    year = year_opt or today.year
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    # if no year in title and date is far in the past, try next year
    if year_opt is None:
        if d < today - timedelta(days=400):
            try:
                d = date(year + 1, month, day)
            except ValueError:
                pass
        elif d < today - timedelta(days=60):
            try:
                d2 = date(year + 1, month, day)
                if abs((d2 - today).days) < abs((d - today).days):
                    d = d2
            except ValueError:
                pass
    return d


def parse_highest_temp_title(title: str) -> Optional[ParsedTempMarket]:
    raw = (title or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if "highest temperature" not in low:
        return None
    city = _extract_city_from_title(raw)
    if not city:
        return None
    city_key = city.strip().lower()
    tz_name = CITY_TZ.get(city_key)
    if not tz_name:
        return None

    m = _DATE_TAIL.search(raw)
    if not m:
        return None
    mon_s = m.group(1).lower()
    day_i = int(m.group(2))
    year_opt = int(m.group(3)) if m.group(3) else None
    month = _MONTHS.get(mon_s)
    if not month:
        return None
    event_date = _resolve_event_date(month, day_i, year_opt, tz_name)
    if not event_date:
        return None

    bracket: Optional[BracketKind] = None
    threshold: Optional[float] = None  # stored in °C (F titles converted)
    mb = _RE_BELOW.search(raw)
    ma = _RE_ABOVE.search(raw)
    me = _RE_EXACT_ON.search(raw)
    if mb:
        bracket = BracketKind.AT_MOST
        threshold = float(mb.group(1))
    elif ma:
        bracket = BracketKind.AT_LEAST
        threshold = float(ma.group(1))
    elif me:
        bracket = BracketKind.EXACT
        threshold = float(me.group(1))
    else:
        m2 = re.search(r"be\s+(\d+(?:\.\d+)?)\s*°?\s*c\b", raw, re.IGNORECASE)
        if m2:
            bracket = BracketKind.EXACT
            threshold = float(m2.group(1))

    if bracket is None:
        mb_f = _RE_BELOW_F.search(raw)
        ma_f = _RE_ABOVE_F.search(raw)
        m_between_f = _RE_BETWEEN_RANGE_F.search(raw)
        mr_f = _RE_RANGE_F.search(raw)
        me_f = _RE_EXACT_F_ON.search(raw)
        if mb_f:
            bracket = BracketKind.AT_MOST
            threshold = _fahrenheit_to_celsius(float(mb_f.group(1)))
        elif ma_f:
            bracket = BracketKind.AT_LEAST
            threshold = _fahrenheit_to_celsius(float(ma_f.group(1)))
        elif m_between_f:
            bracket = BracketKind.EXACT
            lo_f = float(m_between_f.group(1))
            hi_f = float(m_between_f.group(2))
            threshold = _fahrenheit_to_celsius((lo_f + hi_f) / 2.0)
        elif mr_f:
            bracket = BracketKind.EXACT
            lo_f = float(mr_f.group(1))
            hi_f = float(mr_f.group(2))
            threshold = _fahrenheit_to_celsius((lo_f + hi_f) / 2.0)
        elif me_f:
            bracket = BracketKind.EXACT
            threshold = _fahrenheit_to_celsius(float(me_f.group(1)))
        else:
            mf2 = re.search(r"be\s+(\d+(?:\.\d+)?)\s*°?\s*f\b", raw, re.IGNORECASE)
            if mf2:
                bracket = BracketKind.EXACT
                threshold = _fahrenheit_to_celsius(float(mf2.group(1)))

    if bracket is None or threshold is None:
        return None

    return ParsedTempMarket(
        city_key=city_key,
        tz_name=tz_name,
        event_date=event_date,
        bracket=bracket,
        threshold_c=threshold,
        raw_title=raw,
    )


def forecast_supports_yes(
    forecast_max_c: float,
    p: ParsedTempMarket,
    *,
    exact_slack_c: Optional[float] = None,
) -> bool:
    """
    Rough check: does daily max temp forecast support YES on this bracket?
    Uses slack for model vs observation error.
    """
    slack = 1.0
    if p.bracket == BracketKind.AT_MOST:
        return forecast_max_c <= p.threshold_c + slack
    if p.bracket == BracketKind.AT_LEAST:
        return forecast_max_c >= p.threshold_c - slack
    ex = float(exact_slack_c or C.FORECAST_EXACT_BUCKET_SUPPORT_SLACK_C)
    return abs(forecast_max_c - p.threshold_c) <= ex


def forecast_contradicts_strongly(
    forecast_max_c: float, p: ParsedTempMarket, margin_c: float
) -> bool:
    """True if forecast clearly disagrees with YES (for hard gate)."""
    if p.bracket == BracketKind.AT_MOST:
        return forecast_max_c > p.threshold_c + margin_c
    if p.bracket == BracketKind.AT_LEAST:
        return forecast_max_c < p.threshold_c - margin_c
    return abs(forecast_max_c - p.threshold_c) > margin_c + 0.5
