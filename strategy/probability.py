from typing import Any, Dict, Optional


def parse_market_probability(market: Dict[str, Any]) -> float:
    # prefer outcomePrices (YES element) — most accurate gamma signal
    op = market.get("outcomePrices")
    if op:
        try:
            import json as _json
            prices = _json.loads(op) if isinstance(op, str) else op
            if isinstance(prices, list) and prices:
                val = float(prices[0])
                if 0 < val <= 1:
                    return val
        except (TypeError, ValueError):
            pass
    # then bestAsk — the real price you'd pay to buy
    for field in ("bestAsk", "bestBid", "lastTradePrice", "price", "probability", "yesPrice"):
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
    stop_loss_threshold: float,
) -> Optional[float]:
    gamma_p = parse_market_probability(market)
    mark = float((trade or {}).get("last_price") or 0)
    # include mark==0 (resolved loser) and stale gamma still high
    price_weak = gamma_p < stop_loss_threshold or mark < stop_loss_threshold
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
