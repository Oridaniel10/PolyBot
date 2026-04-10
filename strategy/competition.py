from typing import Any, Dict, List, Tuple

from polymarket_client import PolymarketClient, gamma_event_ids_for_market

from config.settings import RuntimeSettings
from strategy.probability import parse_market_probability


def competition_runner_up_yes(
    client: PolymarketClient,
    market: Dict[str, Any],
    cache: Dict[str, List[Dict[str, Any]]],
) -> Tuple[float, bool]:
    """
    returns (runner_up_yes_probability, has_event_context).
    if no event siblings resolved, has_event_context False — caller may allow buy.
    """
    eids = gamma_event_ids_for_market(market)
    if not eids:
        return 0.0, False
    eid = eids[0]
    mid = str(market.get("id") or "").strip()
    if eid not in cache:
        cache[eid] = client.get_markets_for_event_id(eid)
    siblings = cache.get(eid) or []
    if not siblings:
        return 0.0, False
    runner = 0.0
    for m in siblings:
        smid = str(m.get("id") or "").strip()
        if smid == mid:
            continue
        p = parse_market_probability(m)
        if p > runner:
            runner = p
    return runner, True


def passes_competition_lead_gap(
    client: PolymarketClient,
    market: Dict[str, Any],
    candidate_yes: float,
    settings: RuntimeSettings,
    cache: Dict[str, List[Dict[str, Any]]],
) -> Tuple[bool, float, bool]:
    """
    returns (allowed, runner_up, used_filter).
    used_filter False when competition filter off or no event context.
    """
    if not settings.enable_competition_filter:
        return True, 0.0, False
    runner, has_ctx = competition_runner_up_yes(client, market, cache)
    if not has_ctx:
        return True, runner, False
    if candidate_yes - runner < settings.min_lead_over_runner_up - 1e-9:
        return False, runner, True
    return True, runner, True
