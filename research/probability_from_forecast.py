"""Map a point forecast (°C) + Gaussian noise to rough P(YES) per bracket (research only)."""

import math
from typing import List, Tuple

from forecast.parse_title import BracketKind, ParsedTempMarket


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def prob_normal_between(a: float, b: float, mu: float, sigma: float) -> float:
    """P(a < T < b) for T ~ N(mu, sigma^2). assumes a < b."""
    if sigma <= 1e-9:
        return 1.0 if a < mu < b else 0.0
    return max(0.0, min(1.0, _phi((b - mu) / sigma) - _phi((a - mu) / sigma)))


def implied_yes_prob(
    p: ParsedTempMarket,
    forecast_mean_c: float,
    sigma_c: float = 2.0,
) -> float:
    """
    Crude Gaussian belief over daily max °C.

    returns:
    - approximate P(this YES bracket would resolve) given mean/sigma.
    """
    mu = forecast_mean_c
    sig = max(0.5, float(sigma_c))
    if p.bracket == BracketKind.AT_LEAST:
        lo = p.threshold_c - 0.5
        return max(0.0, min(1.0, 1.0 - _phi((lo - mu) / sig)))

    if p.bracket == BracketKind.AT_MOST:
        hi = p.threshold_c + 0.5
        return max(0.0, min(1.0, _phi((hi - mu) / sig)))

    # EXACT: integer °C window around threshold (whole degrees)
    c = p.threshold_c
    lo_b = math.floor(c - 0.5)
    hi_b = math.ceil(c + 0.5)
    if hi_b <= lo_b:
        lo_b, hi_b = c - 0.5, c + 0.5
    return prob_normal_between(lo_b, hi_b, mu, sig)


def rank_brackets_by_model(
    parsed_list: List[ParsedTempMarket],
    forecast_mean_c: float,
    sigma_c: float = 2.0,
) -> List[Tuple[str, float]]:
    """returns (short label, implied_prob) sorted desc."""
    scored: List[Tuple[str, float]] = []
    for p in parsed_list:
        pr = implied_yes_prob(p, forecast_mean_c, sigma_c=sigma_c)
        label = (p.raw_title or "")[:120]
        scored.append((label, pr))
    scored.sort(key=lambda x: -x[1])
    return scored
