"""Open-Meteo forecast: daily temperature_2m_max (no API key)."""

from datetime import date
from typing import Optional

import requests

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 12


def fetch_daily_max_temp_c(
    lat: float,
    lon: float,
    target: date,
    timezone: str,
) -> Optional[float]:
    """
    returns forecast daily max °C for `target` in given IANA timezone, or None.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "timezone": timezone,
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
    }
    try:
        r = requests.get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    vals = daily.get("temperature_2m_max") or []
    if not times or not vals:
        return None
    want = target.isoformat()
    for i, t in enumerate(times):
        if str(t)[:10] == want[:10] and i < len(vals):
            try:
                return float(vals[i])
            except (TypeError, ValueError):
                return None
    return None
