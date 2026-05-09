"""pair reversal: target YES rises while a sibling YES falls (crowd shift)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from strategy.momentum_engine import absolute_price_change_in_window


def detect_pair_reversal(
    target_market_id: str,
    sibling_market_ids: List[str],
    *,
    window_sec: float = 600.0,
    min_target_rise: float = 0.08,
    min_sibling_drop: float = 0.08,
    min_target_pct: float = 0.50,
    min_sibling_drop_pct: float = 0.30,
    min_target_current: float = 0.05,
    max_target_current: float = 0.80,
    now_ts: Optional[float] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """true when target rose and some sibling dropped enough in window.

    params:
    - target_market_id: market we might buy or track.
    - sibling_market_ids: other buckets in same gamma event.
    - window_sec: lookback.
    - min_target_rise / min_target_pct: target must rise by abs pts or pct fraction.
    - min_sibling_drop / min_sibling_drop_pct: best sibling drop must qualify.
    - min_target_current,max_target_current: guardrails on newest price.

    returns:
    - (signal_bool, meta with prices, deltas, sibling id, failure reason tag).
    """
    now_ts = now_ts or time.time()
    t_old, t_new, t_abs = absolute_price_change_in_window(
        target_market_id, window_sec, now_ts, min_samples=2
    )
    meta: Dict[str, Any] = {
        "target_old": t_old,
        "target_new": t_new,
        "target_change_pts": t_abs,
        "target_change_pct": 0.0,
        "falling_sibling_id": "",
        "sibling_change_pts": 0.0,
        "sibling_change_pct": 0.0,
        "reason": "no_signal",
    }
    if t_old <= 1e-9:
        meta["reason"] = "target_no_baseline"
        return False, meta
    if t_new + 1e-12 < min_target_current or t_new > max_target_current + 1e-12:
        meta["reason"] = "target_outside_band"
        return False, meta

    t_pct = (t_new - t_old) / t_old
    meta["target_change_pct"] = t_pct

    target_rising = (t_abs >= min_target_rise) or (t_pct >= min_target_pct)
    if not target_rising:
        meta["reason"] = "target_not_rising"
        return False, meta

    worst_sibling = ""
    worst_abs_drop = 0.0
    worst_pct_drop = 0.0
    tid = str(target_market_id).strip()
    for sid in sibling_market_ids:
        sid = str(sid).strip()
        if not sid or sid == tid:
            continue
        s_old, s_new, _s_abs_chg = absolute_price_change_in_window(
            sid, window_sec, now_ts, min_samples=2
        )
        if s_old <= 1e-9:
            continue
        drop_pts = float(s_old - s_new)
        drop_pct = drop_pts / s_old if s_old > 0 else 0.0
        if drop_pts > worst_abs_drop:
            worst_abs_drop = drop_pts
            worst_pct_drop = drop_pct
            worst_sibling = sid

    sibling_falling = (worst_abs_drop >= min_sibling_drop) or (
        worst_pct_drop >= min_sibling_drop_pct
    )
    meta["falling_sibling_id"] = worst_sibling
    meta["sibling_change_pts"] = float(-worst_abs_drop)
    meta["sibling_change_pct"] = float(-worst_pct_drop)

    if not sibling_falling:
        meta["reason"] = "no_falling_sibling"
        return False, meta

    meta["reason"] = "pair_reversal"
    return True, meta
