"""Open-Meteo archive + historical forecast for research (no API key)."""

import time
from datetime import date
from typing import Callable, Optional

import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HIST_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 22
RETRY_ATTEMPTS = 3
RETRY_SLEEP_SEC = 0.45


def fetch_daily_max_with_retries(
    fn: Callable[..., Optional[float]],
    *,
    lat: float,
    lon: float,
    target: date,
    timezone: str,
    attempts: int = RETRY_ATTEMPTS,
    sleep_sec: float = RETRY_SLEEP_SEC,
) -> Optional[float]:
    """retry transient Open-Meteo empty/error responses."""
    last: Optional[float] = None
    n = max(1, int(attempts))
    for i in range(n):
        last = fn(lat, lon, target, timezone)
        if last is not None:
            return last
        if i + 1 < n:
            time.sleep(max(0.0, sleep_sec))
    return last


def _daily_max_from_response(data: dict, target: date) -> Optional[float]:
    if not isinstance(data, dict):
        return None
    daily = data.get("daily") or {}
    times = daily.get("time") or []
    vals = daily.get("temperature_2m_max") or []
    want = target.isoformat()
    for i, t in enumerate(times):
        if str(t)[:10] == want[:10] and i < len(vals):
            try:
                return float(vals[i])
            except (TypeError, ValueError):
                return None
    return None


def fetch_archive_daily_max_c(
    lat: float,
    lon: float,
    target: date,
    timezone: str,
) -> Optional[float]:
    """ERA5-Land (or similar) daily max — fallback when WU 30d API misses the date."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "timezone": timezone,
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
    }
    try:
        r = requests.get(ARCHIVE_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return _daily_max_from_response(r.json(), target)
    except (requests.RequestException, ValueError, TypeError):
        return None


def fetch_historical_forecast_daily_max_c(
    lat: float,
    lon: float,
    target: date,
    timezone: str,
) -> Optional[float]:
    """Past model run daily max for `target` (historical forecast product)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "timezone": timezone,
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
    }
    try:
        r = requests.get(HIST_FORECAST_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return _daily_max_from_response(r.json(), target)
    except (requests.RequestException, ValueError, TypeError):
        return None


def fetch_forecast_daily_max_c(
    lat: float,
    lon: float,
    target: date,
    timezone: str,
) -> Optional[float]:
    """Operational forecast snapshot (same as forecast/open_meteo.py)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "timezone": timezone,
        "start_date": target.isoformat(),
        "end_date": target.isoformat(),
    }
    try:
        r = requests.get(FORECAST_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return _daily_max_from_response(r.json(), target)
    except (requests.RequestException, ValueError, TypeError):
        return None
