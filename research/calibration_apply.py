"""Load data/research/calibration_latest.json and adjust consensus °C."""

import math
from typing import Optional

from research.jsonl_util import load_json_file
from config import constants as C

# E|Z| for Z ~ N(0,1) = sqrt(2/pi); MAE of mean forecast as location estimate ~ sigma * E|Z|
_MAE_TO_SIGMA = 1.0 / math.sqrt(2.0 / math.pi)


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


def mae_global() -> float:
    """
    returns:
    - global MAE (°C) for open_meteo_forecast vs truth from calibration_latest.json.
    """
    cal = load_calibration_dict()
    models = cal.get("models") or {}
    om = models.get("open_meteo_forecast") or {}
    mae = float(om.get("mae_c", 0.0) or 0.0)
    if mae <= 1e-9:
        return 1.5
    return mae


def sigma_from_mae(mae_c: float) -> float:
    """maps forecast MAE (°C) to a Gaussian sigma for P(YES) over buckets."""
    return max(0.5, float(mae_c) * _MAE_TO_SIGMA)


def resolved_research_sigma_c(sigma_setting: float, city_key: str) -> float:
    """
    returns:
    - explicit sigma from settings if sigma_setting > 0 (clamped 0.5..8),
    - else sigma derived from global MAE (city_key reserved for future per-city MAE).
    """
    _ = city_key
    s = float(sigma_setting)
    if s > 1e-9:
        return max(0.5, min(8.0, s))
    return sigma_from_mae(mae_global())
