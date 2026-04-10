"""Map city names (from market titles) to IANA timezones for local-hour gating."""

import re
from datetime import datetime
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]

# covers every city that appears in Polymarket highest-temperature markets
CITY_TZ = {
    "amsterdam": "Europe/Amsterdam",
    "ankara": "Europe/Istanbul",
    "atlanta": "America/New_York",
    "austin": "America/Chicago",
    "beijing": "Asia/Shanghai",
    "buenos aires": "America/Argentina/Buenos_Aires",
    "busan": "Asia/Seoul",
    "chengdu": "Asia/Shanghai",
    "chicago": "America/Chicago",
    "chongqing": "Asia/Shanghai",
    "dallas": "America/Chicago",
    "denver": "America/Denver",
    "helsinki": "Europe/Helsinki",
    "hong kong": "Asia/Hong_Kong",
    "houston": "America/Chicago",
    "istanbul": "Europe/Istanbul",
    "jakarta": "Asia/Jakarta",
    "kuala lumpur": "Asia/Kuala_Lumpur",
    "london": "Europe/London",
    "los angeles": "America/Los_Angeles",
    "lucknow": "Asia/Kolkata",
    "madrid": "Europe/Madrid",
    "mexico city": "America/Mexico_City",
    "miami": "America/New_York",
    "milan": "Europe/Rome",
    "moscow": "Europe/Moscow",
    "munich": "Europe/Berlin",
    "new york city": "America/New_York",
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "panama city": "America/Panama",
    "paris": "Europe/Paris",
    "san francisco": "America/Los_Angeles",
    "sao paulo": "America/Sao_Paulo",
    "seattle": "America/Los_Angeles",
    "seoul": "Asia/Seoul",
    "shanghai": "Asia/Shanghai",
    "shenzhen": "Asia/Shanghai",
    "singapore": "Asia/Singapore",
    "taipei": "Asia/Taipei",
    "tel aviv": "Asia/Jerusalem",
    "tokyo": "Asia/Tokyo",
    "toronto": "America/Toronto",
    "warsaw": "Europe/Warsaw",
    "wellington": "Pacific/Auckland",
    "wuhan": "Asia/Shanghai",
}


def _extract_city_from_title(title: str) -> str:
    """Extract city name from 'Will the highest temperature in <city> be ...'"""
    t = title.lower()
    for prefix in ("will the highest temperature in ", "highest temperature in "):
        if t.startswith(prefix):
            rest = t[len(prefix):]
            for sep in (" be ", " on "):
                idx = rest.find(sep)
                if idx > 0:
                    return rest[:idx].strip()
    return ""


def city_local_hour(title: str) -> Optional[int]:
    """Return the current local hour (0-23) for the city in the market title,
    or None if city/timezone can't be determined."""
    if ZoneInfo is None:
        return None
    city = _extract_city_from_title(title)
    if not city:
        return None
    tz_name = CITY_TZ.get(city)
    if not tz_name:
        return None
    try:
        return datetime.now(ZoneInfo(tz_name)).hour
    except Exception:
        return None


def city_local_time_str(title: str) -> str:
    """Return 'HH:MM' in the city's local timezone, or '' if unknown."""
    if ZoneInfo is None:
        return ""
    city = _extract_city_from_title(title)
    if not city:
        return ""
    tz_name = CITY_TZ.get(city)
    if not tz_name:
        return ""
    try:
        return datetime.now(ZoneInfo(tz_name)).strftime("%H:%M")
    except Exception:
        return ""
