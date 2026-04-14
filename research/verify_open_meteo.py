"""Smoke-test Open-Meteo stack + full-city archive coverage (research / bot)."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import List, Optional, Tuple

import requests

from forecast.geocode import CITY_LAT_LON, city_lat_lon
from research.cities_catalog import load_research_city_keys
from research.open_meteo_research import (
    fetch_archive_daily_max_c,
    fetch_forecast_daily_max_c,
    fetch_historical_forecast_daily_max_c,
)
from strategy.city_tz import CITY_TZ

GEOCODE_URL = (
    "https://geocoding-api.open-meteo.com/v1/search"  # API endpoint for geocoding
)
GEOCODE_TIMEOUT = 15


def check_city_tables() -> Tuple[bool, List[str]]:
    """every parsed Polymarket temp city (CITY_TZ) must have static lat/lon."""
    missing = sorted(set(CITY_TZ.keys()) - set(CITY_LAT_LON.keys()))
    return (not missing, missing)


def smoke_geocode_chicago() -> bool:
    r = requests.get(
        GEOCODE_URL,
        params={"name": "Chicago", "count": 1, "language": "en", "format": "json"},
        timeout=GEOCODE_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    res = data.get("results") if isinstance(data, dict) else None
    return isinstance(res, list) and len(res) > 0 and "latitude" in res[0]


def run_verify(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify CITY_TZ↔lat/lon sync and Open-Meteo endpoints"
    )
    ap.add_argument(
        "--archive-days-ago",
        type=int,
        default=7,
        help="test archive for this many days before UTC today (ERA5 publication lag)",
    )
    args = ap.parse_args(argv)

    ok, missing = check_city_tables()
    if not ok:
        print("FAIL: CITY_TZ keys without CITY_LAT_LON:", missing)
        return 1
    print(f"OK: CITY_TZ / CITY_LAT_LON aligned ({len(CITY_TZ)} cities)")

    try:
        if not smoke_geocode_chicago():
            print("FAIL: Open-Meteo geocoding returned no results for Chicago")
            return 1
        print("OK: Open-Meteo geocoding API (Chicago)")
    except requests.RequestException as e:
        print(f"FAIL: geocoding HTTP {e}")
        return 1

    tgt = date.today() - timedelta(days=max(1, int(args.archive_days_ago)))
    lat, lon, tz = 41.88, -87.63, "America/Chicago"
    triples = [
        ("archive (ERA5 daily max)", fetch_archive_daily_max_c(lat, lon, tgt, tz)),
        (
            "historical_forecast",
            fetch_historical_forecast_daily_max_c(lat, lon, tgt, tz),
        ),
        ("forecast API", fetch_forecast_daily_max_c(lat, lon, tgt, tz)),
    ]
    for label, val in triples:
        if val is None:
            print(f"FAIL: {label} returned None for Chicago {tgt}")
            return 1
        print(f"OK: {label} → {val} °C (Chicago {tgt})")

    catalog = load_research_city_keys()
    failed_cities: List[str] = []
    for city in catalog:
        ck = city.strip().lower()
        ll = city_lat_lon(ck)
        if not ll:
            failed_cities.append(f"{ck} (no static lat/lon)")
            continue
        tzn = CITY_TZ.get(ck)
        if not tzn:
            failed_cities.append(f"{ck} (no CITY_TZ)")
            continue
        v = fetch_archive_daily_max_c(ll[0], ll[1], tgt, tzn)
        if v is None:
            failed_cities.append(ck)

    if failed_cities:
        print(
            f"WARN: archive daily max missing for {len(failed_cities)} cities at {tgt} "
            f"(ERA5 lag or API); e.g. {failed_cities[:8]}"
        )
        return 2

    print(f"OK: archive temperature_2m_max for all {len(catalog)} cities ({tgt})")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_verify(sys.argv[1:]))
