"""leader-yield gate for momentum: target rises + sibling bleed OR collective bleed."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from config import constants as C
from strategy.momentum_engine import absolute_price_change_in_window


def parse_trigger_window_seconds(trigger: str) -> Optional[float]:
    """map labels like 15m_win → 900.0 seconds."""
    raw = str(trigger or "").strip().lower()
    if not raw.endswith("_win"):
        return None
    pref = raw[:-4].strip()
    if not pref.endswith("m"):
        return None
    try:
        mins = float(pref[:-1])
        return max(1.0, mins * 60.0)
    except ValueError:
        return None


def leader_yield_drop_qualifies(
    *,
    target_market_id: str,
    event_market_ids: List[str],
    window_sec: float,
    min_leader_old_price: float,
    min_fall_abs_pts: float,
    min_fall_frac_of_old: float,
    min_samples: int,
    collective_fall_min_abs_pts: Optional[float] = None,
    collective_fall_min_frac: Optional[float] = None,
    now_ts: Optional[float] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """target rises on W; then either (A) best single-sibling fall or (B) collective fall."""
    now_ts = now_ts or time.time()
    tid = str(target_market_id).strip()

    c_abs = float(
        C.COLLECTIVE_FALL_MIN_ABS_PTS
        if collective_fall_min_abs_pts is None
        else collective_fall_min_abs_pts
    )
    c_frac = float(
        C.COLLECTIVE_FALL_MIN_FRAC
        if collective_fall_min_frac is None
        else collective_fall_min_frac
    )
    c_abs = max(0.0, min(2.0, c_abs))
    c_frac = max(0.0, min(1.0, c_frac))

    meta: Dict[str, Any] = {
        "leader_id": "",
        "leader_old": 0.0,
        "leader_new": 0.0,
        "drop_pts": 0.0,
        "drop_frac": 0.0,
        "reason": "no_signal",
        "collective_total_drop_pts": 0.0,
        "collective_total_old": 0.0,
        "collective_drop_frac": 0.0,
        "pass_condition": "",
        "candidates_evaluated": 0,
    }

    uniq: List[str] = []
    seen: set[str] = set()
    for raw in event_market_ids:
        mid = str(raw).strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        uniq.append(mid)

    if len(uniq) < 2:
        meta["reason"] = "event_too_few_buckets"
        _log_leader_yield_v2(tid, False, meta, "")
        return False, meta

    tgt_old, tgt_new, _ = absolute_price_change_in_window(
        tid, float(window_sec), now_ts, min_samples=min_samples
    )
    if tgt_old <= 1e-12 or tgt_new <= tgt_old + 1e-12:
        meta["reason"] = "target_not_rising"
        _log_leader_yield_v2(tid, False, meta, "")
        return False, meta

    sibling_rows: List[Tuple[str, float, float]] = []
    for mid in uniq:
        if mid == tid:
            continue
        old_p, new_p, _chg = absolute_price_change_in_window(
            mid, float(window_sec), now_ts, min_samples=min_samples
        )
        if old_p > float(min_leader_old_price) + 1e-12:
            sibling_rows.append((mid, float(old_p), float(new_p)))
            continue
        drop_reason = (
            "insufficient_samples_or_low_price"
            if old_p <= 1e-12
            else "below_min_leader_old_price"
        )
        print(
            f"[leader_yield] dropped sibling mid={mid} "
            f"old_p={float(old_p):.4f} new_p={float(new_p):.4f} "
            f"window_sec={float(window_sec):.0f} target={tid} reason={drop_reason}"
        )

    meta["candidates_evaluated"] = len(sibling_rows)

    total_old = 0.0
    total_drop_pts = 0.0
    per_drop: List[Tuple[str, float, float, float, float]] = []
    for mid, old_p, new_p in sibling_rows:
        total_old += old_p
        dp = max(0.0, old_p - new_p)
        total_drop_pts += dp
        dfrac = dp / old_p if old_p > 1e-12 else 0.0
        per_drop.append((mid, old_p, new_p, dp, dfrac))

    meta["collective_total_old"] = round(float(total_old), 6)
    meta["collective_total_drop_pts"] = round(float(total_drop_pts), 6)
    cdf = (total_drop_pts / total_old) if total_old > 1e-12 else 0.0
    meta["collective_drop_frac"] = round(float(cdf), 6)

    if not sibling_rows:
        meta["reason"] = "no_peer_baseline_leader"
        _log_leader_yield_v2(tid, False, meta, "")
        return False, meta

    pass_b = total_drop_pts + 1e-12 >= c_abs or cdf + 1e-12 >= c_frac

    best = max(per_drop, key=lambda x: x[3])
    best_mid, bo, bn, bdp, bdfrac = best
    pass_a = bdp + 1e-12 >= float(min_fall_abs_pts) or bdfrac + 1e-12 >= float(
        min_fall_frac_of_old
    )

    largest_indiv = max(per_drop, key=lambda x: x[3])

    if pass_a:
        meta["leader_id"] = best_mid
        meta["leader_old"] = float(bo)
        meta["leader_new"] = float(bn)
        meta["drop_pts"] = round(float(bdp), 6)
        meta["drop_frac"] = round(float(bdfrac), 6)
        meta["pass_condition"] = "A"
        meta["reason"] = "ok"
        _log_leader_yield_v2(tid, True, meta, "A")
        return True, meta

    if pass_b:
        lm = largest_indiv[0]
        meta["leader_id"] = lm
        meta["leader_old"] = float(largest_indiv[1])
        meta["leader_new"] = float(largest_indiv[2])
        meta["drop_pts"] = round(float(largest_indiv[3]), 6)
        meta["drop_frac"] = round(float(largest_indiv[4]), 6)
        meta["pass_condition"] = "B"
        meta["reason"] = "ok"
        _log_leader_yield_v2(tid, True, meta, "B")
        return True, meta

    meta["leader_id"] = best_mid
    meta["leader_old"] = float(bo)
    meta["leader_new"] = float(bn)
    meta["drop_pts"] = round(float(bdp), 6)
    meta["drop_frac"] = round(float(bdfrac), 6)
    meta["reason"] = "no_qualifying_fall"
    _log_leader_yield_v2(tid, False, meta, "")
    return False, meta


def _log_leader_yield_v2(
    tid: str,
    passed: bool,
    meta: Dict[str, Any],
    cond: str,
) -> None:
    best_y = str(meta.get("leader_id") or "")
    bdp = float(meta.get("drop_pts") or 0.0)
    bdf = float(meta.get("drop_frac") or 0.0)
    cdp = float(meta.get("collective_total_drop_pts") or 0.0)
    cdf = float(meta.get("collective_drop_frac") or 0.0)
    n_c = int(meta.get("candidates_evaluated") or 0)
    rs = str(meta.get("reason") or "")
    cc = cond or str(meta.get("pass_condition") or "none")
    if passed and not cc:
        cc = str(meta.get("pass_condition") or "none")
    print(
        f"[leader_yield_v2] target={tid} passed={passed} condition={cc} "
        f"best_faller={best_y} best_drop_pts={bdp:.4f} best_drop_frac={bdf:.4f} "
        f"collective_drop_pts={cdp:.4f} collective_drop_frac={cdf:.4f} "
        f"candidates={n_c} reason={rs}"
    )


def market_title_by_id(siblings: List[Dict[str, Any]], mid: str) -> str:
    """best-effort bracket title from cached gamma rows."""
    ms = str(mid).strip()
    for m in siblings:
        if not isinstance(m, dict):
            continue
        if str(m.get("id") or "").strip() != ms:
            continue
        return str(m.get("question") or m.get("title") or m.get("slug") or ms).strip()[
            :200
        ]
    return ms


def is_persistent_leader(
    market_id: str,
    event_market_ids: List[str],
    lookback_sec: float = 7200.0,
    min_first_place_fraction: float = 0.80,
    now_ts: Optional[float] = None,
) -> bool:
    """Return True if market_id held the highest YES price among siblings for
    at least min_first_place_fraction of the price samples in the lookback window.

    Used as an alternative buy trigger (Section 6): a persistent leader that now
    shows a momentum jump can be bought without requiring sibling fall (leader-yield).
    """
    from strategy.momentum import load_samples_for_market

    mid = str(market_id or "").strip()
    if not mid:
        return False
    now = now_ts if now_ts is not None else time.time()
    window = max(120.0, float(lookback_sec))

    # load this market's samples
    own_samples = load_samples_for_market(mid, window, now)
    if len(own_samples) < 3:
        return False

    # collect all sibling IDs (excluding the candidate itself)
    sibling_ids = [
        str(s).strip()
        for s in event_market_ids
        if str(s).strip() and str(s).strip() != mid
    ]
    if not sibling_ids:
        # single-bucket event — trivially always #1, but not a meaningful signal
        return False

    # for each own sample timestamp, find highest sibling price via interpolation
    # load sibling samples once
    sibling_samples: Dict[str, List[Tuple[float, float]]] = {}
    for sid in sibling_ids:
        pts = load_samples_for_market(sid, window, now)
        if pts:
            sibling_samples[sid] = pts

    if not sibling_samples:
        return False

    def _interp_price(samples: List[Tuple[float, float]], ts: float) -> float:
        """Linear interpolation of price at ts from sorted (ts, price) list."""
        if not samples:
            return 0.0
        if ts <= samples[0][0]:
            return float(samples[0][1])
        if ts >= samples[-1][0]:
            return float(samples[-1][1])
        for i in range(len(samples) - 1):
            t0, p0 = samples[i]
            t1, p1 = samples[i + 1]
            if t0 <= ts <= t1:
                frac = (ts - t0) / max(1e-6, t1 - t0)
                return float(p0 + frac * (p1 - p0))
        return float(samples[-1][1])

    first_place_count = 0
    total_count = 0
    for ts, own_price in own_samples:
        max_sibling_price = max(
            _interp_price(pts, ts) for pts in sibling_samples.values()
        )
        total_count += 1
        if own_price >= max_sibling_price - 1e-6:
            first_place_count += 1

    if total_count < 3:
        return False
    fraction = first_place_count / total_count
    return fraction >= float(min_first_place_fraction)
