"""Resolution registry rows from Gamma markets (parsing only, no HTTP)."""

import re
from typing import Any, Dict, Optional

from config import constants as C
from forecast.parse_title import parse_highest_temp_title

# optional ICAO-like code from wunderground URL text in rules (no network)
STATION_FROM_WU_PATH_RE = re.compile(
    r"wunderground\.com/history/daily/[^/\s]+/[^/\s]+/([A-Za-z0-9]{3,5})\b",
    re.IGNORECASE,
)


def extract_station_code_hint(text: str) -> str:
    """station code if rules mention a wunderground daily URL (informational only)."""
    if not (text or "").strip():
        return ""
    m = STATION_FROM_WU_PATH_RE.search(text)
    return m.group(1).upper() if m else ""


def title_uses_fahrenheit(title: str) -> bool:
    t = (title or "").lower()
    return "°f" in t or "° f" in t or " f " in t and "°" in t


def resolution_row_from_market(
    market: Dict[str, Any],
    *,
    gamma_event_id: str = "",
) -> Dict[str, Any]:
    """
    registry row for modeling: no truth_url / no scrape targets.
    """
    title = str(market.get("question") or market.get("title") or "")
    desc = str(market.get("description") or "")
    rsrc = str(market.get("resolutionSource") or "")
    combined = f"{desc}\n{rsrc}"
    p = parse_highest_temp_title(title)
    mid = str(market.get("id") or "").strip()
    cid = str(
        market.get("conditionId")
        or market.get("condition_id")
        or market.get("questionID")
        or market.get("questionId")
        or ""
    ).strip()
    row: Dict[str, Any] = {
        "market_id": mid,
        "condition_id": cid,
        "gamma_event_id": str(gamma_event_id or "").strip(),
        "city_key": "",
        "event_date": "",
        "bracket": "",
        "threshold_c": None,
        "unit": "F" if title_uses_fahrenheit(title) else "C",
        "precision_integer": True,
        "station_code_hint": extract_station_code_hint(combined),
        "question_snip": title[: int(C.RESEARCH_REGISTRY_RSRC_MAX_LEN)],
    }
    if p:
        row["city_key"] = p.city_key.lower()
        row["event_date"] = p.event_date.isoformat()
        row["bracket"] = p.bracket.value
        row["threshold_c"] = round(float(p.threshold_c), 4)
    return row
