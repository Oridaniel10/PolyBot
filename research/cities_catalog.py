"""Canonical research city list + coordinate validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from config import constants as C
from forecast.geocode import city_lat_lon
from strategy.city_tz import CITY_TZ


def load_research_city_keys() -> List[str]:
    """
    ordered city_key list from data/research/cities.json, else sorted CITY_TZ keys.
    """
    path = Path(C.RESEARCH_CITIES_FILE)
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            out = [str(x).strip().lower() for x in raw if str(x).strip()]
            return out
    return sorted(CITY_TZ.keys())


def verify_all_cities_have_coordinates(city_keys: List[str]) -> List[str]:
    """
    returns human-readable problems (must be empty to proceed).
    """
    problems: List[str] = []
    for ck in city_keys:
        k = (ck or "").strip().lower()
        if not k:
            continue
        if k not in CITY_TZ:
            problems.append(f"{k}: missing CITY_TZ entry")
        if city_lat_lon(k) is None:
            problems.append(f"{k}: missing CITY_LAT_LON entry")
    seen = set()
    out: List[str] = []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out
