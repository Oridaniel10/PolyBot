"""Cached forecast fetch: Open-Meteo + optional OpenWeather consensus."""

import os
import time
from datetime import date
from typing import Dict, Optional, Tuple

from forecast.geocode import city_lat_lon
from forecast.open_meteo import fetch_daily_max_temp_c as fetch_om
from forecast.openweather import fetch_daily_max_temp_c as fetch_ow

_CACHE: Dict[
    Tuple[str, str],
    Tuple[Optional[float], Optional[float], Optional[float], float],
] = {}
_CACHE_TTL_SEC = 580.0


def consensus_two(
    a: Optional[float], b: Optional[float]
) -> Optional[float]:
    vals = [x for x in (a, b) if x is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def get_forecast_max_for_city_day(
    city_key: str,
    event_date: date,
    tz_name: str,
    *,
    openweather_api_key: str = "",
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    returns (open_meteo_max, openweather_max_or_none, consensus).
    """
    now = time.monotonic()
    ck = (city_key.lower(), event_date.isoformat())
    hit = _CACHE.get(ck)
    if hit and (now - hit[3]) < _CACHE_TTL_SEC:
        return hit[0], hit[1], consensus_two(hit[0], hit[1])

    ll = city_lat_lon(city_key)
    if not ll:
        return None, None, None
    lat, lon = ll
    om = fetch_om(lat, lon, event_date, tz_name)
    ow_key = (openweather_api_key or os.getenv("OPENWEATHER_API_KEY", "")).strip()
    ow = fetch_ow(lat, lon, event_date, ow_key, tz_name) if ow_key else None
    cons = consensus_two(om, ow)
    # do not cache all-None (transient API / network); avoids "no forecast" stuck ~10min
    if om is not None or ow is not None:
        _CACHE[ck] = (om, ow, cons, now)
    return om, ow, cons


def clear_forecast_cache() -> None:
    _CACHE.clear()
