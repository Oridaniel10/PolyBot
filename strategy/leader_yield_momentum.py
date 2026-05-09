"""leader-yield gate for momentum entry: ex-leader sibling must fall sharply in-window."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

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
    now_ts: Optional[float] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """True when the YES leader at window *start* fell enough vs its *new* YES.

    Leader = argmax oldest-sample YES among event buckets (excluding illiquid 0).
    If target was that leader at start, this gate fails (need ex-leader bleed).
    Fall leg: abs drop ≥ min_fall_abs_pts OR fractional drop ≥ min_fall_frac_of_old.
    """
    now_ts = now_ts or time.time()
    tid = str(target_market_id).strip()
    meta: Dict[str, Any] = {
        "leader_id": "",
        "leader_old": 0.0,
        "leader_new": 0.0,
        "drop_pts": 0.0,
        "drop_frac": 0.0,
        "reason": "no_signal",
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
        return False, meta

    rows: List[Tuple[str, float, float]] = []
    for mid in uniq:
        old_p, new_p, _chg = absolute_price_change_in_window(
            mid, float(window_sec), now_ts, min_samples=min_samples
        )
        if old_p > float(min_leader_old_price) + 1e-12:
            rows.append((mid, float(old_p), float(new_p)))

    if not rows:
        meta["reason"] = "no_peer_baseline_leader"
        return False, meta

    rows.sort(key=lambda r: (-r[1], r[0]))
    leader_id, old_l, new_l = rows[0]
    meta["leader_id"] = leader_id
    meta["leader_old"] = old_l
    meta["leader_new"] = new_l

    if leader_id == tid:
        meta["reason"] = "target_was_leader_at_window_start"
        return False, meta

    drop_pts = max(0.0, float(old_l - new_l))
    drop_frac = drop_pts / old_l if old_l > 1e-12 else 0.0
    meta["drop_pts"] = round(float(drop_pts), 6)
    meta["drop_frac"] = round(float(drop_frac), 6)

    fell_enough = drop_pts + 1e-12 >= float(min_fall_abs_pts) or drop_frac + 1e-12 >= float(
        min_fall_frac_of_old
    )
    if not fell_enough:
        meta["reason"] = "leader_fall_below_threshold"
        return False, meta

    meta["reason"] = "ok"
    return True, meta


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
