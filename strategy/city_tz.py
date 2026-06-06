"""Map city names (from market titles) to IANA timezones for local-hour gating."""

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
    "cape town": "Africa/Johannesburg",
    "dallas": "America/Chicago",
    "denver": "America/Denver",
    "helsinki": "Europe/Helsinki",
    "hong kong": "Asia/Hong_Kong",
    "houston": "America/Chicago",
    "istanbul": "Europe/Istanbul",
    "jakarta": "Asia/Jakarta",
    "jeddah": "Asia/Riyadh",
    "kuala lumpur": "Asia/Kuala_Lumpur",
    "lagos": "Africa/Lagos",
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


def city_local_now(title: str) -> Optional[datetime]:
    """Return current local datetime for the city in the title, or None if unknown."""
    if ZoneInfo is None:
        return None
    city = _extract_city_from_title(title)
    if not city:
        return None
    tz_name = CITY_TZ.get(city.strip().lower())
    if not tz_name:
        return None
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return None


def city_local_hour(title: str) -> Optional[int]:
    """Return the current local hour (0-23) for the city in the market title,
    or None if city/timezone can't be determined."""
    dt = city_local_now(title)
    return dt.hour if dt else None


def city_local_time_str(title: str) -> str:
    """Return 'HH:MM' in the city's local timezone, or '' if unknown."""
    dt = city_local_now(title)
    return dt.strftime("%H:%M") if dt else ""


def city_local_datetime_now_str(title: str) -> str:
    """Return 'YYYY-MM-DD HH:MM' (city local clock now), or '' if unknown."""
    dt = city_local_now(title)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def format_hour_float_as_hhmm(hour_float: float) -> str:
    """Convert fractional-hour to 'HH:MM' display string.

    Examples:
        15.0  -> '15:00'
        15.5  -> '15:30'
        15.25 -> '15:15'
        16.75 -> '16:45'
    """
    if hour_float is None:
        return ""
    try:
        h_float = float(hour_float)
    except (TypeError, ValueError):
        return ""
    # clamp to 0..24
    h_float = max(0.0, min(24.0, h_float))
    h = int(h_float)
    m = int(round((h_float - h) * 60.0))
    if m == 60:
        h += 1
        m = 0
    return f"{h:02d}:{m:02d}"
