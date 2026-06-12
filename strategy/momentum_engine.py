"""15-minute momentum tracker for fast exit, competitor surge, and momentum entry.

Replaces the old momentum.py with proper min/max tracking and rate-of-change
computation instead of first/last approximation.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

from config import constants as C
from strategy.probability import parse_market_probability
from strategy.momentum import (
    load_sample_window_for_market,
    load_samples_for_market,
)


def price_change_in_window(
    market_id: str,
    window_sec: float = 900.0,
    now_ts: Optional[float] = None,
) -> Tuple[float, float, float]:
    """
    compute price change over window.

    returns:
    - (oldest_price, newest_price, change_pct) where change_pct is fractional.
    """
    now_ts = now_ts or time.time()
    series = load_samples_for_market(market_id, window_sec, now_ts)
    if len(series) < 2:
        return 0.0, 0.0, 0.0
    old_price = series[0][1]
    new_price = series[-1][1]
    if old_price <= 1e-9:
        return old_price, new_price, 0.0
    change = (new_price - old_price) / old_price
    return old_price, new_price, change


def absolute_price_change_in_window(
    market_id: str,
    window_sec: float = 900.0,
    now_ts: Optional[float] = None,
    min_samples: int = 2,
) -> Tuple[float, float, float]:
    """
    compute absolute YES-price change over window.

    returns:
    - (oldest_price, newest_price, change_points).
    """
    now_ts = now_ts or time.time()
    window = load_sample_window_for_market(market_id, window_sec, now_ts)
    if window.count < max(2, int(min_samples)):
        return 0.0, 0.0, 0.0
    return (
        window.oldest_price,
        window.newest_price,
        window.newest_price - window.oldest_price,
    )


def max_drawdown_in_window(
    market_id: str,
    window_sec: float = 900.0,
    now_ts: Optional[float] = None,
) -> float:
    """Max peak-to-trough decline in window as ABSOLUTE price points.

    e.g. peak 0.75, trough 0.59 → drawdown = 0.16.
    """
    now_ts = now_ts or time.time()
    series = load_samples_for_market(market_id, window_sec, now_ts)
    if len(series) < 2:
        return 0.0
    peak = series[0][1]
    max_dd = 0.0
    for _, price in series:
        if price > peak:
            peak = price
        dd = peak - price
        if dd > max_dd:
            max_dd = dd
    return max_dd


def max_drawdown_since_ts(
    market_id: str,
    since_ts: float,
    window_sec: float = 900.0,
    now_ts: Optional[float] = None,
) -> float:
    """Max peak-to-trough decline since entry, capped by the window."""
    now_ts = now_ts or time.time()
    series = load_samples_for_market(market_id, window_sec, now_ts)
    if len(series) < 2:
        return 0.0
    start_ts = max(float(since_ts or 0.0), float(now_ts) - float(window_sec))
    scoped = [(ts, price) for ts, price in series if float(ts) >= start_ts]
    if len(scoped) < 2:
        return 0.0
    peak = scoped[0][1]
    max_dd = 0.0
    for _, price in scoped:
        if price > peak:
            peak = price
        dd = peak - price
        if dd > max_dd:
            max_dd = dd
    return max_dd


def should_fast_exit(
    market_id: str,
    drop_threshold: float = 0.15,
    window_sec: float = 900.0,
    now_ts: Optional[float] = None,
) -> Tuple[bool, float]:
    """
    check if our position dropped more than threshold in the window.

    returns:
    - (should_exit, drawdown_pct).
    """
    # needs enough price_samples in the window; a sudden cliff without prior highs
    # can understate drawdown vs intuitive "lost 99%" — see STRATEGY_LOGIC.md exits.
    dd = max_drawdown_in_window(market_id, window_sec, now_ts)
    return dd >= drop_threshold, dd


def should_fast_exit_since_ts(
    market_id: str,
    since_ts: float,
    drop_threshold: float = 0.15,
    window_sec: float = 900.0,
    now_ts: Optional[float] = None,
) -> Tuple[bool, float]:
    """Check drawdown only from entry time onward."""
    dd = max_drawdown_since_ts(market_id, since_ts, window_sec, now_ts)
    return dd >= drop_threshold, dd


def peer_surge_detected(
    peer_market_ids: List[str],
    surge_threshold: float = 0.15,
    window_sec: float = 900.0,
    now_ts: Optional[float] = None,
) -> Tuple[bool, str, float]:
    """
    check if any peer bucket surged above threshold.

    returns:
    - (detected, surging_market_id, rise_points).
    """
    now_ts = now_ts or time.time()
    best_rise = 0.0
    best_mid = ""
    for pid in peer_market_ids:
        pid = str(pid).strip()
        if not pid:
            continue
        _, _, change = absolute_price_change_in_window(
            pid,
            window_sec,
            now_ts,
            min_samples=C.MOMENTUM_MIN_SAMPLE_POINTS,
        )
        if change > best_rise:
            best_rise = change
            best_mid = pid
    return best_rise >= surge_threshold, best_mid, best_rise


def momentum_entry_signal(
    market_id: str,
    rise_threshold: float = 0.15,
    window_sec: float = 900.0,
    now_ts: Optional[float] = None,
) -> Tuple[bool, float]:
    """
    check if this bucket has positive momentum for entry.

    returns:
    - (signal_active, rise_points).
    """
    _, _, change = absolute_price_change_in_window(
        market_id,
        window_sec,
        now_ts,
        min_samples=C.MOMENTUM_MIN_SAMPLE_POINTS,
    )
    return change >= rise_threshold, change


def momentum_entry_signal_with_pct(
    market_id: str,
    abs_rise_threshold: float,
    pct_rise_threshold: float,
    window_sec: float,
    min_start_price: float,
    min_current_price: float,
    max_current_price: float,
    now_ts: Optional[float] = None,
) -> Tuple[bool, float, float, float, float, str]:
    """Evaluate momentum with absolute or percent rise plus guardrails."""
    old_price, new_price, abs_change = absolute_price_change_in_window(
        market_id,
        window_sec,
        now_ts,
        min_samples=C.MOMENTUM_MIN_SAMPLE_POINTS,
    )
    if old_price <= 1e-9:
        return False, old_price, new_price, abs_change, 0.0, "insufficient_samples"
    pct_change = (new_price - old_price) / old_price
    if old_price + 1e-12 < float(min_start_price):
        return (
            False,
            old_price,
            new_price,
            abs_change,
            pct_change,
            "start_price_too_low",
        )
    if new_price + 1e-12 < float(min_current_price):
        return (
            False,
            old_price,
            new_price,
            abs_change,
            pct_change,
            "current_price_too_low",
        )
    if new_price - 1e-12 > float(max_current_price):
        return (
            False,
            old_price,
            new_price,
            abs_change,
            pct_change,
            "current_price_too_high",
        )
    passed = (abs_change >= float(abs_rise_threshold)) or (
        pct_change >= float(pct_rise_threshold)
    )
    return passed, old_price, new_price, abs_change, pct_change, "ok"


def yes_rank_by_market_prob(market_id: str, siblings: List[Dict[str, Any]]) -> int:
    """
    1-based rank by gamma/market YES (1 = highest). missing id → large rank.
    """
    rows: List[Tuple[str, float]] = []
    for m in siblings:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        rows.append((mid, parse_market_probability(m)))
    if not rows:
        return 999
    rows.sort(key=lambda x: -x[1])
    for i, (mid, _) in enumerate(rows, start=1):
        if mid == market_id:
            return i
    return 999


def top_price_change_peers(
    market_ids: List[str],
    window_sec: float = 900.0,
    now_ts: Optional[float] = None,
    top_n: int = 3,
) -> List[Tuple[str, float]]:
    """
    peers sorted by 15m absolute price change (best first).

    returns:
    - up to top_n (market_id, change_points) pairs.
    """
    now_ts = now_ts or time.time()
    out: List[Tuple[str, float]] = []
    for raw in market_ids:
        mid = str(raw).strip()
        if not mid:
            continue
        _, _, ch = absolute_price_change_in_window(
            mid,
            window_sec,
            now_ts,
            min_samples=C.MOMENTUM_MIN_SAMPLE_POINTS,
        )
        out.append((mid, ch))
    out.sort(key=lambda x: -x[1])
    return out[: max(0, int(top_n))]
