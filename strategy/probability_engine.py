"""Gaussian probability model over temperature buckets.

Computes P(YES) per bucket using calibrated forecast + city-specific MAE sigma.
Single source of truth for model probabilities used by the decision engine.
"""

from typing import Any, Dict, List, Tuple

from forecast.parse_title import ParsedTempMarket, parse_highest_temp_title
from research.calibration_apply import (
    resolved_research_sigma_c,
)
from research.probability_from_forecast import implied_yes_prob
from strategy.probability import parse_market_probability


def compute_model_prob(
    parsed: ParsedTempMarket,
    consensus_c: float,
    sigma_c_setting: float = 0.0,
) -> float:
    sigma = resolved_research_sigma_c(sigma_c_setting, parsed.city_key)
    return implied_yes_prob(parsed, consensus_c, sigma_c=sigma)


def compute_all_bucket_probs(
    event_markets: List[Dict[str, Any]],
    consensus_c: float,
    sigma_c_setting: float = 0.0,
) -> List[Tuple[str, float, float]]:
    """
    compute model probability for every bucket in an event.

    returns:
    - list of (market_id, model_prob, market_prob) sorted by model_prob descending.
    """
    results: List[Tuple[str, float, float]] = []
    for m in event_markets:
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        title = str(m.get("question") or m.get("title") or "")
        parsed = parse_highest_temp_title(title)
        if not parsed:
            continue
        mp = compute_model_prob(parsed, consensus_c, sigma_c_setting)
        mkt_p = parse_market_probability(m)
        results.append((mid, mp, mkt_p))
    results.sort(key=lambda x: -x[1])
    return results


def bucket_edge(model_prob: float, market_prob: float) -> float:
    return model_prob - market_prob


def get_sigma_for_city(city_key: str, sigma_c_setting: float = 0.0) -> float:
    return resolved_research_sigma_c(sigma_c_setting, city_key)
