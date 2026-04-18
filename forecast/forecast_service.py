"""Cached forecast fetch: Open-Meteo daily max; optional OpenWeather blend."""

import sys
from pathlib import Path

# allow `python3 forecast/forecast_service.py` (repo root on sys.path)
_rs = str(Path(__file__).resolve().parent.parent)
if _rs not in sys.path:
    sys.path.insert(0, _rs)

import os
import time
from datetime import date
from typing import Dict, Optional, Tuple

from forecast.geocode import city_lat_lon
from forecast.open_meteo import fetch_daily_max_temp_c as fetch_om
from forecast.openweather import fetch_daily_max_temp_c as fetch_ow
from research.calibration_apply import adjust_consensus_optional

_CACHE: Dict[
    Tuple[str, str, bool],
    Tuple[Optional[float], Optional[float], Optional[float], float],
] = {}
_CACHE_TTL_SEC = 580.0


def consensus_two(a: Optional[float], b: Optional[float]) -> Optional[float]:
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
    use_openweather: bool = False,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    returns (open_meteo_max, openweather_max_or_none, consensus_adjusted).

    consensus is Open-Meteo only unless use_openweather is true and a
    non-empty API key is available (caller key or OPENWEATHER_API_KEY env),
    then mean(OM, OW). Calibration applies to that value.
    """
    now = time.monotonic()
    blend = bool(use_openweather)
    ck = (city_key.lower(), event_date.isoformat(), blend)
    hit = _CACHE.get(ck)
    if hit and (now - hit[3]) < _CACHE_TTL_SEC:
        # hit[2] is already calibration-adjusted consensus
        return hit[0], hit[1], hit[2]

    ll = city_lat_lon(city_key)
    if not ll:
        return None, None, None
    lat, lon = ll
    om = fetch_om(lat, lon, event_date, tz_name)
    ow: Optional[float] = None
    if blend:
        ow_key = (openweather_api_key or os.getenv("OPENWEATHER_API_KEY", "")).strip()
        if ow_key:
            ow = fetch_ow(lat, lon, event_date, ow_key, tz_name)
    cons = consensus_two(om, ow) if blend else om
    # do not cache all-None (transient API / network); avoids "no forecast" stuck ~10min
    cons_adj = adjust_consensus_optional(cons, city_key)
    if om is not None or ow is not None:
        _CACHE[ck] = (om, ow, cons_adj, now)
    return om, ow, cons_adj


def clear_forecast_cache() -> None:
    _CACHE.clear()
