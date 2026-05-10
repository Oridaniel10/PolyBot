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
        return str(
            m.get("question") or m.get("title") or m.get("slug") or ms
        ).strip()[:200]
    return ms
