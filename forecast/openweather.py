"""Optional OpenWeather 5-day / 3-hour forecast → daily max on target local date."""

from datetime import date, datetime
from typing import Optional

import requests

OW_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
REQUEST_TIMEOUT = 12


def fetch_daily_max_temp_c(
    lat: float,
    lon: float,
    target: date,
    api_key: str,
    tz_name: str,
) -> Optional[float]:
    if not api_key:
        return None
    try:
        r = requests.get(
            OW_FORECAST_URL,
            params={
                "lat": lat,
                "lon": lon,
                "appid": api_key,
                "units": "metric",
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError, TypeError):
        return None
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None
    mx: Optional[float] = None
    for item in data.get("list") or []:
        if not isinstance(item, dict):
            continue
        main = item.get("main") or {}
        temp = main.get("temp_max") or main.get("temp")
        if temp is None:
            continue
        dt_u = item.get("dt")
        if not dt_u:
            continue
        try:
            local_d = datetime.fromtimestamp(int(dt_u), tz=tz).date()
        except (TypeError, ValueError, OSError):
            continue
        if local_d != target:
            continue
        try:
            v = float(temp)
        except (TypeError, ValueError):
            continue
        mx = v if mx is None else max(mx, v)
    return mx
