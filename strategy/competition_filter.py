"""Enhanced competition filter: leader must lead by >= threshold over runner-up.

Uses both model probabilities and market probabilities for a robust comparison.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from polymarket_client import PolymarketClient, gamma_event_ids_for_market
from strategy.probability import parse_market_probability
from strategy.probability_engine import compute_all_bucket_probs


@dataclass(frozen=True)
class CompetitionResult:
    passes: bool
    leader_market_id: str
    leader_prob: float
    runner_up_prob: float
    gap: float
    method: str


def evaluate_competition(
    client: PolymarketClient,
    market: Dict[str, Any],
    candidate_market_prob: float,
    min_lead_gap: float,
    event_cache: Dict[str, List[Dict[str, Any]]],
    consensus_c: Optional[float] = None,
    sigma_c_setting: float = 0.0,
) -> CompetitionResult:
    """
    check if this bucket leads by at least min_lead_gap over the runner-up.

    uses model probs when consensus is available, falls back to market probs.
    """
    mid = str(market.get("id") or "").strip()
    eids = gamma_event_ids_for_market(market)
    if not eids:
        return CompetitionResult(
            passes=True, leader_market_id=mid,
            leader_prob=candidate_market_prob, runner_up_prob=0.0,
            gap=candidate_market_prob, method="no_event_context",
        )

    eid = str(eids[0]).strip()
    if eid not in event_cache:
        event_cache[eid] = client.get_markets_for_event_id(eid)
    siblings = event_cache.get(eid) or []
    if not siblings:
        return CompetitionResult(
            passes=True, leader_market_id=mid,
            leader_prob=candidate_market_prob, runner_up_prob=0.0,
            gap=candidate_market_prob, method="no_siblings",
        )

    if consensus_c is not None:
        bucket_probs = compute_all_bucket_probs(
            siblings, consensus_c, sigma_c_setting
        )
        if len(bucket_probs) >= 2:
            candidate_model_p = 0.0
            for bmid, mp, _ in bucket_probs:
                if bmid == mid:
                    candidate_model_p = mp
                    break
            best_other = 0.0
            for bmid, mp, _ in bucket_probs:
                if bmid != mid and mp > best_other:
                    best_other = mp
            gap = candidate_model_p - best_other
            return CompetitionResult(
                passes=gap >= min_lead_gap - 1e-9,
                leader_market_id=mid,
                leader_prob=candidate_model_p,
                runner_up_prob=best_other,
                gap=gap,
                method="model_probs",
            )

    runner_up = 0.0
    for m in siblings:
        smid = str(m.get("id") or "").strip()
        if smid == mid:
            continue
        p = parse_market_probability(m)
        if p > runner_up:
            runner_up = p
    gap = candidate_market_prob - runner_up
    return CompetitionResult(
        passes=gap >= min_lead_gap - 1e-9,
        leader_market_id=mid,
        leader_prob=candidate_market_prob,
        runner_up_prob=runner_up,
        gap=gap,
        method="market_probs",
    )


def market_yes_lead_gap_vs_runner_up(
    market_id: str,
    candidate_yes: float,
    siblings: List[Dict[str, Any]],
) -> Tuple[float, float]:
    """
    gap = candidate YES minus best other bucket YES (runner-up in the market race).

    returns:
    - (gap, runner_up_yes).
    """
    runner_up = 0.0
    for m in siblings:
        if not isinstance(m, dict):
            continue
        smid = str(m.get("id") or "").strip()
        if not smid or smid == market_id:
            continue
        p = parse_market_probability(m)
        if p > runner_up:
            runner_up = p
    return float(candidate_yes) - runner_up, runner_up
