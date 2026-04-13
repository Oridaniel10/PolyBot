"""Approximate lat/lon for cities in highest-temperature Polymarket markets (Open-Meteo)."""

from typing import Optional, Tuple

from strategy.city_tz import CITY_TZ

# (lat, lon) — city center approximations; keys match CITY_TZ keys
CITY_LAT_LON: dict[str, Tuple[float, float]] = {
    "amsterdam": (52.37, 4.90),
    "ankara": (39.93, 32.86),
    "atlanta": (33.75, -84.39),
    "austin": (30.27, -97.74),
    "beijing": (39.90, 116.41),
    "buenos aires": (-34.60, -58.38),
    "busan": (35.18, 129.08),
    "cape town": (-33.93, 18.42),
    "chengdu": (30.67, 104.07),
    "chicago": (41.88, -87.63),
    "chongqing": (29.56, 106.55),
    "dallas": (32.78, -96.80),
    "denver": (39.74, -104.99),
    "helsinki": (60.17, 24.94),
    "hong kong": (22.32, 114.17),
    "houston": (29.76, -95.37),
    "istanbul": (41.01, 28.98),
    "jakarta": (-6.21, 106.85),
    "jeddah": (21.49, 39.18),
    "kuala lumpur": (3.14, 101.69),
    "lagos": (6.52, 3.38),
    "london": (51.51, -0.13),
    "los angeles": (34.05, -118.24),
    "lucknow": (26.85, 80.95),
    "madrid": (40.42, -3.70),
    "mexico city": (19.43, -99.13),
    "miami": (25.76, -80.19),
    "milan": (45.46, 9.19),
    "moscow": (55.76, 37.62),
    "munich": (48.14, 11.58),
    "new york city": (40.71, -74.01),
    "new york": (40.71, -74.01),
    "nyc": (40.71, -74.01),
    "panama city": (8.98, -79.52),
    "paris": (48.86, 2.35),
    "san francisco": (37.77, -122.42),
    "sao paulo": (-23.55, -46.63),
    "seattle": (47.61, -122.33),
    "seoul": (37.57, 126.98),
    "shanghai": (31.23, 121.47),
    "shenzhen": (22.54, 114.06),
    "singapore": (1.35, 103.82),
    "taipei": (25.03, 121.56),
    "tel aviv": (32.09, 34.78),
    "tokyo": (35.68, 139.76),
    "toronto": (43.65, -79.38),
    "warsaw": (52.23, 21.01),
    "wellington": (-41.29, 174.78),
    "wuhan": (30.59, 114.31),
}


def city_lat_lon(city_key: str) -> Optional[Tuple[float, float]]:
    k = (city_key or "").strip().lower()
    if k in CITY_LAT_LON:
        return CITY_LAT_LON[k]
    return None


def known_city_keys() -> frozenset:
    return frozenset(CITY_TZ.keys())
