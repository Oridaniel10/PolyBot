"""Infer nominal resolved daily-max °C from Polymarket YES winners (Gamma snapshot)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


WIN_YES_MIN = 0.99


def _yes_price_from_gamma(market: Dict[str, Any]) -> Optional[float]:
    op = market.get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except json.JSONDecodeError:
            return None
    if isinstance(op, list) and op:
        try:
            return float(op[0])
        except (TypeError, ValueError):
            return None
    return None


def _market_closed(m: Dict[str, Any]) -> bool:
    status = str(m.get("closed") or m.get("status") or "").lower()
    return bool(m.get("closed")) or status in ("closed", "resolved", "claimable")


def truth_max_c_from_polymarket_event(
    parsed_rows: List[Dict[str, Any]],
) -> Optional[float]:
    """
    if exactly one sibling market is closed with YES price >= WIN_YES_MIN,
    return that bracket threshold_c (°C) as nominal resolved truth.
    """
    winners: List[float] = []
    for row in parsed_rows:
        m = row.get("market") or {}
        p = row.get("parsed")
        if p is None:
            continue
        if not _market_closed(m):
            continue
        yp = _yes_price_from_gamma(m)
        if yp is None or yp < WIN_YES_MIN:
            continue
        try:
            winners.append(float(p.threshold_c))
        except (TypeError, ValueError, AttributeError):
            continue
    if len(winners) != 1:
        return None
    return winners[0]


def group_parsed_rows_by_city_date(
    parsed_rows: List[Dict[str, Any]],
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in parsed_rows:
        p = row.get("parsed")
        if p is None:
            continue
        k = (p.city_key.lower(), p.event_date.isoformat())
        out.setdefault(k, []).append(row)
    return out
