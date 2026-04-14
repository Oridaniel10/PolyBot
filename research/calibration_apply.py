"""Load data/research/calibration_latest.json and adjust consensus °C."""

from typing import Optional

from research.jsonl_util import load_json_file
from config import constants as C


def load_calibration_dict() -> dict:
    return load_json_file(C.RESEARCH_CALIBRATION_LATEST_FILE) or {}


def bias_for_city(city_key: str) -> float:
    """
    returns:
    - additive bias °C for `open_meteo_forecast` consensus (city override or global).
    """
    cal = load_calibration_dict()
    ck = (city_key or "").strip().lower()
    by_city = cal.get("by_city") or {}
    if ck and isinstance(by_city.get(ck), dict):
        return float(by_city[ck].get("bias_c", 0.0))
    models = cal.get("models") or {}
    om = models.get("open_meteo_forecast") or {}
    return float(om.get("bias_c", 0.0))


def adjust_consensus_optional(consensus_c: Optional[float], city_key: str) -> Optional[float]:
    if consensus_c is None:
        return None
    return float(consensus_c) + bias_for_city(city_key)
