from typing import Any, Dict, Optional, Set

# dedupe stale-outcome warnings per process so logs stay one line per market_id.
_STALE_OUTCOME_LOGGED: Set[str] = set()


def _best_ask_from_market(market: Dict[str, Any]) -> Optional[float]:
    v = market.get("bestAsk")
    if v is None:
        return None
    try:
        x = float(v)
        if x <= 0:
            return None
        return x if x <= 1 else x / 100.0
    except (TypeError, ValueError):
        return None


def stop_loss_mark_bar_for_entry(
    entry: float,
    *,
    use_tiers: bool,
    tier_split: float,
    mark_if_entry_below_split: float,
    mark_if_entry_at_or_above_split: float,
    legacy_flat: float,
) -> float:
    """
    picks the probability mark below which we treat YES as "weak" for stop-loss.

    params:
    - entry: yes entry price (0–1).
    - use_tiers: if false, use legacy_flat only.
    - tier_split: entry threshold between low and high tier bars.
    - mark_if_entry_below_split: weak bar when entry < tier_split.
    - mark_if_entry_at_or_above_split: weak bar when entry >= tier_split.
    - legacy_flat: single bar when use_tiers is false.

    returns:
    - stop-loss comparison threshold in (0, 1).
    """
    e = float(entry)
    if use_tiers:
        sp = float(tier_split)
        if e + 1e-12 < sp:
            return float(mark_if_entry_below_split)
        return float(mark_if_entry_at_or_above_split)
    return float(legacy_flat)


def parse_market_probability(market: Dict[str, Any]) -> float:
    mid = str(market.get("id") or "").strip()
    # prefer outcomePrices (YES element) — most accurate gamma signal
    op = market.get("outcomePrices")
    if op:
        try:
            import json as _json

            prices = _json.loads(op) if isinstance(op, str) else op
            if isinstance(prices, list) and prices:
                val = float(prices[0])
                if 0 < val <= 1:
                    ba = _best_ask_from_market(market)
                    if (
                        mid
                        and mid not in _STALE_OUTCOME_LOGGED
                        and val <= 0.01
                        and ba is not None
                        and ba >= 0.10
                    ):
                        print(
                            "[stale_outcome_prices] "
                            f"mid={mid} outcomePrices_yes={val:.4f} bestAsk={ba:.4f}"
                        )
                        _STALE_OUTCOME_LOGGED.add(mid)
                    return val
        except (TypeError, ValueError):
            pass
    # then bestAsk — the real price you'd pay to buy
    for field in (
        "bestAsk",
        "bestBid",
        "lastTradePrice",
        "price",
        "probability",
        "yesPrice",
    ):
        value = market.get(field)
        if value is None:
            continue
        try:
            parsed = float(value)
            if parsed <= 0:
                continue
            return parsed if parsed <= 1 else parsed / 100.0
        except (TypeError, ValueError):
            continue
    return 0.0


def take_profit_decision_probability(
    market: Dict[str, Any], trade: Optional[Dict[str, Any]]
) -> float:
    gamma_p = parse_market_probability(market)
    if not trade:
        return gamma_p
    mark = float(trade.get("last_price") or 0)
    return max(gamma_p, mark)


def stop_loss_reference_if_triggered(
    market: Dict[str, Any],
    trade: Optional[Dict[str, Any]],
    stop_loss_mark_bar: float,
) -> Optional[float]:
    gamma_p = parse_market_probability(market)
    mark = float((trade or {}).get("last_price") or 0)
    bar = float(stop_loss_mark_bar)
    # include mark==0 (resolved loser) and stale gamma still high
    price_weak = gamma_p < bar or mark < bar
    if not price_weak:
        return None

    entry = float((trade or {}).get("entry_price") or 0)
    if entry <= 1e-9:
        return None
    # underwater: mark below entry (mark 0 counts as full loss vs long YES)
    in_loss = mark < entry - 1e-9
    if not in_loss:
        return None

    if mark > 1e-9:
        return min(gamma_p, mark)
    return gamma_p
