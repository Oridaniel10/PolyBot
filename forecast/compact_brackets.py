"""Pick a tiny subset of sibling brackets for Telegram (1st / 2nd / last YES)."""

from __future__ import annotations

from typing import Any, Dict, List

from strategy.probability import parse_market_probability


def sort_markets_by_yes_desc(markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(markets, key=lambda m: -parse_market_probability(m))


def pick_first_second_last_yes(markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    highest YES, 2nd YES, lowest YES — keeps chat short vs listing every bracket.
    """
    mks = sort_markets_by_yes_desc(markets)
    n = len(mks)
    if n <= 2:
        return list(mks)
    first, second = mks[0], mks[1]
    last = mks[-1]
    ids = {str(first.get("id") or ""), str(second.get("id") or "")}
    lid = str(last.get("id") or "")
    if lid and lid not in ids:
        return [first, second, last]
    for m in reversed(mks):
        mid = str(m.get("id") or "")
        if mid and mid not in ids:
            return [first, second, m]
    return [first, second]
