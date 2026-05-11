"""Central decision orchestrator — combines probability, momentum, competition, time filters.

Replaces strategy/decision_engine.py with a stricter, smarter system that:
- Trades LESS (strong filtering before entry)
- Trades SMARTER (combines model + market + momentum)
- Avoids losing streaks (time-decay, fast stop-loss, competitor surge)

historical misses (e.g. paris 22°c held while 21°c surged, milan 23 vs 24):
- max_positions_per_event=1 blocked buying the surging sibling while still holding.
- momentum entry required buy_min (e.g. 0.55) instead of the wide momentum band.
- model/flat ceilings ran before momentum could qualify. event_buy_cooldown after
  competitor-surge exit blocked re-entry into the same gamma event for 20 minutes.
  rotation is handled by momentum_switch (sell held) plus relaxed momentum gates.

rotation when holding: the event market-yes leader may trigger a switch when its
YES is >= held_yes + MOMENTUM_SWITCH_ABOVE_HELD_GAP plus absolute momentum rise
and momentum price band (see detect_momentum_switch / momentum_competitor_dominates_held_exit).
"""

from __future__ import annotations

import collections
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from notifications.terminal import TERM_DIM, term_wrap

from config import constants as C
from config.settings import RuntimeSettings
from forecast.parse_title import ParsedTempMarket
from polymarket_client import PolymarketClient, gamma_event_ids_for_market
from research.calibration_apply import resolved_research_sigma_c
from strategy.competition_filter import (
    CompetitionResult,
    evaluate_competition,
    market_yes_lead_gap_vs_runner_up,
)
from strategy.momentum_engine import (
    absolute_price_change_in_window,
    momentum_entry_signal,
    momentum_entry_signal_with_pct,
    peer_surge_detected,
    should_fast_exit,
    should_fast_exit_since_ts,
    top_price_change_peers,
    yes_rank_by_market_prob,
)
from strategy.leader_yield_momentum import (
    leader_yield_drop_qualifies,
    market_title_by_id,
    parse_trigger_window_seconds,
)
from strategy.momentum import load_samples_for_market
from strategy.probability import parse_market_probability
from strategy.probability_engine import bucket_edge, compute_model_prob
from strategy.research_signal import (
    ResearchEdgeDecision,
    research_edge_decision,
)
from strategy.time_filter import should_time_decay_exit

DECISION_LOG_MAX = 200
_decision_log: Deque[Dict[str, Any]] = collections.deque(maxlen=DECISION_LOG_MAX)


def momentum_window_sec(settings: RuntimeSettings) -> float:
    w = float(getattr(settings, "momentum_window_seconds", C.MOMENTUM_WINDOW_SECONDS))
    return max(120.0, min(7200.0, w))


def momentum_fast_window_sec(settings: RuntimeSettings) -> float:
    w = float(
        getattr(
            settings, "momentum_fast_window_seconds", C.MOMENTUM_FAST_WINDOW_SECONDS
        )
    )
    return max(60.0, min(3600.0, w))


def double_momentum_fast_window_sec(settings: RuntimeSettings) -> float:
    w = float(
        getattr(
            settings,
            "double_momentum_fast_window_seconds",
            C.DOUBLE_MOMENTUM_FAST_WINDOW_SECONDS,
        )
    )
    return max(60.0, min(3600.0, w))


def momentum_fast_exit_drop(settings: RuntimeSettings) -> float:
    d = float(getattr(settings, "momentum_fast_exit_drop", C.MOMENTUM_FAST_EXIT_DROP))
    return max(0.01, min(0.95, d))


def seconds_since_entry_iso(
    entry_time_utc: str,
    *,
    now_ts: Optional[float] = None,
) -> Optional[float]:
    """seconds since iso entry stamp, or none if missing/unparseable."""
    raw = str(entry_time_utc or "").strip()
    if not raw:
        return None
    try:
        ent = datetime.fromisoformat(raw).timestamp()
    except (ValueError, TypeError):
        try:
            ent = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError, AttributeError):
            return None
    if ent <= 0:
        return None
    now = float(time.time() if now_ts is None else now_ts)
    return max(0.0, now - ent)


def momentum_competitor_surge_thr(settings: RuntimeSettings) -> float:
    s = float(
        getattr(settings, "momentum_competitor_surge", C.MOMENTUM_COMPETITOR_SURGE)
    )
    return max(0.01, min(0.95, s))


def momentum_entry_rise_thr(settings: RuntimeSettings) -> float:
    r = float(getattr(settings, "momentum_entry_rise", C.MOMENTUM_ENTRY_RISE))
    return max(0.01, min(0.95, r))


def momentum_entry_pct_rise_thr(settings: RuntimeSettings) -> float:
    r = float(getattr(settings, "momentum_pct_rise", C.MOMENTUM_PCT_RISE))
    return max(0.01, min(10.0, r))


def double_momentum_entry_pct_rise_thr(settings: RuntimeSettings) -> float:
    r = float(getattr(settings, "double_momentum_pct_rise", C.DOUBLE_MOMENTUM_PCT_RISE))
    return max(0.01, min(10.0, r))


def momentum_multi_window_check(
    *,
    market_id: str,
    abs_rise_threshold: float,
    pct_rise_threshold: float,
    windows_sec: List[float],
    min_start_price: float,
    min_current_price: float,
    max_current_price: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    passed = False
    trigger = "none"
    meta = {
        "std_passed": False,
        "std_abs_rise": 0.0,
        "std_pct_rise": 0.0,
        "std_old_price": 0.0,
        "std_new_price": 0.0,
    }

    for w in sorted(windows_sec):
        w_passed, w_old, w_new, w_abs, w_pct, w_reason = momentum_entry_signal_with_pct(
            market_id=market_id,
            abs_rise_threshold=abs_rise_threshold,
            pct_rise_threshold=pct_rise_threshold,
            window_sec=w,
            min_start_price=min_start_price,
            min_current_price=min_current_price,
            max_current_price=max_current_price,
        )
        if w_passed and not passed:
            passed = True
            trigger = f"{int(w // 60)}m_win"
            meta.update(
                {
                    "std_passed": True,
                    "std_abs_rise": float(w_abs),
                    "std_pct_rise": float(w_pct),
                    "std_old_price": float(w_old),
                    "std_new_price": float(w_new),
                    "std_window_sec": float(w),
                }
            )

    return passed, trigger, meta


def momentum_leader_same_window_check(
    *,
    market_id: str,
    event_market_ids: List[str],
    windows_sec: List[float],
    abs_rise_threshold: float,
    pct_rise_threshold: float,
    min_start_price: float,
    min_current_price: float,
    max_current_price: float,
    min_leader_old_price: float,
    min_fall_abs_pts: float,
    min_fall_frac_of_old: float,
    min_samples: int,
    collective_fall_min_abs_pts: Optional[float] = None,
    collective_fall_min_frac: Optional[float] = None,
) -> Tuple[bool, str, Dict[str, Any], Dict[str, Any]]:
    """Independent-window check: target rise in ANY window AND sibling fall in ANY window.

    Rise and fall windows are evaluated independently — a sibling that fell 5 minutes ago
    combined with our price rising over the last 10 minutes both count, even though the
    windows are different.  The trigger is labelled by the winning rise window.
    """
    empty_meta: Dict[str, Any] = {
        "std_passed": False,
        "std_abs_rise": 0.0,
        "std_pct_rise": 0.0,
        "std_old_price": 0.0,
        "std_new_price": 0.0,
        "std_window_sec": 0.0,
    }
    fail_leader: Dict[str, Any] = {"reason": "no_momentum_rise_in_grid"}

    # Pass 1: find the smallest window where our rise qualifies
    rise_win: Optional[float] = None
    rise_meta: Dict[str, Any] = {}
    for w in sorted(windows_sec):
        w_passed, w_old, w_new, w_abs, w_pct, _wr = momentum_entry_signal_with_pct(
            market_id=market_id,
            abs_rise_threshold=abs_rise_threshold,
            pct_rise_threshold=pct_rise_threshold,
            window_sec=w,
            min_start_price=min_start_price,
            min_current_price=min_current_price,
            max_current_price=max_current_price,
        )
        if w_passed:
            rise_win = w
            rise_meta = {
                "std_passed": True,
                "std_abs_rise": float(w_abs),
                "std_pct_rise": float(w_pct),
                "std_old_price": float(w_old),
                "std_new_price": float(w_new),
                "std_window_sec": float(w),
            }
            break

    if rise_win is None:
        return False, "none", empty_meta, fail_leader

    # Pass 2: find ANY window where sibling fall qualifies (independent of rise window)
    for w_fall in sorted(windows_sec):
        ok, lmd = leader_yield_drop_qualifies(
            target_market_id=market_id,
            event_market_ids=list(event_market_ids),
            window_sec=float(w_fall),
            min_leader_old_price=min_leader_old_price,
            min_fall_abs_pts=min_fall_abs_pts,
            min_fall_frac_of_old=min_fall_frac_of_old,
            min_samples=min_samples,
            collective_fall_min_abs_pts=collective_fall_min_abs_pts,
            collective_fall_min_frac=collective_fall_min_frac,
        )
        lmd = dict(lmd)
        lmd["parsed_window_sec"] = float(w_fall)
        lmd["leader_eval_window_sec"] = float(w_fall)
        lmd["rise_window_sec"] = float(rise_win)
        if ok:
            mins = max(1, int(round(rise_win / 60.0)))
            trigger = f"{mins}m_win"
            return True, trigger, rise_meta, lmd
        fail_leader = lmd

    return False, "none", empty_meta, fail_leader


def get_recent_decisions(limit: int = 50) -> List[Dict[str, Any]]:
    return list(reversed(list(_decision_log)))[:limit]


@dataclass(frozen=True)
class TradeDecision:
    city: str
    date_iso: str
    chosen_bucket: str
    model_prob: float
    market_prob: float
    edge: float
    consensus_c: Optional[float]
    sigma_used: float
    decision: str
    reason: str
    momentum_15m: float = 0.0
    competition: Optional[CompetitionResult] = None
    research: Optional[ResearchEdgeDecision] = None
    # true when this BUY used momentum path (wide price band; bypass model/edge/competition)
    momentum_relaxed_gates: bool = False
    event_yes_rank: int = 0
    # entry type determines which stop-loss bar applies:
    # "normal" → stop_loss_normal, "momentum" → stop_loss_momentum,
    # "double_momentum" → stop_loss_double_momentum
    entry_type: str = "normal"
    # which window triggered the momentum signal: "15m_std" / "5m_fast" / "both" / "none"
    trigger_window: str = "none"
    # raw rise metrics on the path that triggered (for trade log + telegram)
    trigger_abs_rise: float = 0.0
    trigger_pct_rise: float = 0.0
    trigger_old_price: float = 0.0
    trigger_new_price: float = 0.0
    # ex-leader sibling that bled — same window as momentum trigger
    leader_fallen_market_id: str = ""
    leader_fallen_market_title: str = ""
    leader_fallen_old_yes: float = 0.0
    leader_fallen_new_yes: float = 0.0
    leader_fallen_drop_pts: float = 0.0
    leader_fallen_drop_frac: float = 0.0
    leader_yield_window_sec: float = 0.0


def momentum_entry_candidate_windows(settings: RuntimeSettings) -> List[float]:
    """Only short windows (≤15m by default) qualify for bot momentum entry."""
    max_sec = float(
        getattr(
            settings,
            "momentum_entry_max_window_seconds",
            C.MOMENTUM_ENTRY_MAX_WINDOW_SEC,
        )
    )
    grid = [60.0 * float(i) for i in range(1, 16)]
    out = [w for w in grid if w <= max_sec + 1e-9]
    return out if out else [float(min(900.0, max_sec))]


def collect_event_id_and_market_ids(
    market: Dict[str, Any],
    event_cache: Dict[str, Any],
    client: Any,
) -> Tuple[Optional[str], Set[str]]:
    eids = gamma_event_ids_for_market(market)
    if not eids:
        return None, set()
    eid = str(eids[0]).strip()
    if not eid:
        return None, set()
    if eid not in event_cache:
        event_cache[eid] = client.get_markets_for_event_id(eid)
    ids: Set[str] = set()
    for m in event_cache.get(eid) or []:
        mid = str(m.get("id") or "").strip()
        if mid:
            ids.add(mid)
    return eid, ids


def event_buy_cooldown_active(state: Dict[str, Any], event_id: str) -> bool:
    if not event_id:
        return False
    raw = state.get("event_buy_cooldown") or {}
    if not isinstance(raw, dict):
        return False
    now = time.time()
    until = float(raw.get(event_id) or 0.0)
    if until <= 0 or now >= until:
        if event_id in raw:
            del raw[event_id]
            state["event_buy_cooldown"] = raw
        return False
    return True


def set_event_buy_cooldown(
    state: Dict[str, Any], event_id: str, cooldown_sec: float
) -> None:
    if not event_id or cooldown_sec <= 0:
        return
    raw = state.setdefault("event_buy_cooldown", {})
    if not isinstance(raw, dict):
        raw = {}
        state["event_buy_cooldown"] = raw
    raw[str(event_id).strip()] = time.time() + float(cooldown_sec)


def leader_market_yes_from_event_markets(markets: Any) -> float:
    best = 0.0
    if not isinstance(markets, list):
        return 0.0
    for m in markets:
        if not isinstance(m, dict):
            continue
        p = parse_market_probability(m)
        if p > best:
            best = p
    return best


def _log_decision(d: TradeDecision) -> None:
    _decision_log.append(
        {
            "ts": time.time(),
            "city": d.city,
            "date_iso": d.date_iso,
            "bucket": d.chosen_bucket[:80],
            "model_prob": round(d.model_prob, 4),
            "market_prob": round(d.market_prob, 4),
            "edge": round(d.edge, 4),
            "momentum_15m": round(d.momentum_15m, 4),
            "decision": d.decision,
            "reason": d.reason[:200],
            "event_yes_rank": int(d.event_yes_rank),
            "momentum_relaxed": bool(d.momentum_relaxed_gates),
        }
    )


MOM_DIAG_SIBLING_WINDOW_SEC = 900.0


def effective_settings_dump_for_momentum_eval(settings: RuntimeSettings) -> Dict[str, Any]:
    """compact dict of effective runtime values feeding momentum_leader_same_window_check."""
    ew = momentum_entry_candidate_windows(settings)
    return {
        "buy_min": float(getattr(settings, "buy_min", C.BUY_MIN_THRESHOLD)),
        "buy_max": float(getattr(settings, "buy_max", C.BUY_MAX_THRESHOLD)),
        "buy_earliest_local_hour": int(
            getattr(settings, "buy_earliest_local_hour", C.BUY_EARLIEST_HOUR)
        ),
        "buy_latest_local_hour": int(getattr(settings, "buy_latest_local_hour", 24)),
        "momentum_window_seconds": round(float(momentum_window_sec(settings)), 2),
        "momentum_entry_max_window_seconds": float(
            getattr(
                settings,
                "momentum_entry_max_window_seconds",
                C.MOMENTUM_ENTRY_MAX_WINDOW_SEC,
            )
        ),
        "momentum_entry_windows_sec": [round(float(x), 2) for x in ew],
        "momentum_entry_rise": round(float(momentum_entry_rise_thr(settings)), 4),
        "momentum_pct_rise": round(float(momentum_entry_pct_rise_thr(settings)), 4),
        "momentum_min_start_price": round(
            float(getattr(settings, "momentum_min_start_price", C.MOMENTUM_MIN_START_PRICE)),
            6,
        ),
        "momentum_min_price": round(
            float(getattr(settings, "momentum_min_price", C.MOMENTUM_MIN_PRICE)), 4
        ),
        "momentum_max_entry": round(
            float(getattr(settings, "momentum_max_entry", C.MOMENTUM_MAX_ENTRY)), 4
        ),
        "double_momentum_entry_rise": round(
            float(
                getattr(settings, "double_momentum_entry_rise", C.DOUBLE_MOMENTUM_ENTRY_RISE)
            ),
            4,
        ),
        "double_momentum_pct_rise": round(
            float(double_momentum_entry_pct_rise_thr(settings)), 4
        ),
        "double_momentum_min_start_price": round(
            float(
                getattr(
                    settings,
                    "double_momentum_min_start_price",
                    C.DOUBLE_MOMENTUM_MIN_START_PRICE,
                )
            ),
            6,
        ),
        "double_momentum_min_price": round(
            float(getattr(settings, "double_momentum_min_price", C.DOUBLE_MOMENTUM_MIN_PRICE)),
            4,
        ),
        "double_momentum_max_price": round(
            float(getattr(settings, "double_momentum_max_price", C.DOUBLE_MOMENTUM_MAX_PRICE)),
            4,
        ),
        "leader_yield_min_leader_old_price": round(
            float(
                getattr(
                    settings,
                    "leader_yield_min_leader_old_price",
                    C.LEADER_YIELD_MIN_LEADER_OLD_PRICE,
                )
            ),
            4,
        ),
        "leader_yield_fall_min_abs_pts": round(
            float(
                getattr(settings, "leader_yield_fall_min_abs_pts", C.LEADER_YIELD_FALL_MIN_ABS_PTS)
            ),
            4,
        ),
        "leader_yield_fall_min_frac": round(
            float(getattr(settings, "leader_yield_fall_min_frac", C.LEADER_YIELD_FALL_MIN_FRAC)),
            4,
        ),
        "collective_fall_min_abs_pts": round(
            float(getattr(settings, "collective_fall_min_abs_pts", C.COLLECTIVE_FALL_MIN_ABS_PTS)),
            4,
        ),
        "collective_fall_min_frac": round(
            float(getattr(settings, "collective_fall_min_frac", C.COLLECTIVE_FALL_MIN_FRAC)),
            4,
        ),
        "min_samples": int(C.MOMENTUM_MIN_SAMPLE_POINTS),
        "research_edge_gate_buy": bool(getattr(settings, "research_edge_gate_buy", False)),
        "enable_competition_filter": bool(getattr(settings, "enable_competition_filter", True)),
        "min_market_yes_lead_gap_normal": round(
            float(
                getattr(
                    settings,
                    "min_market_yes_lead_gap_normal",
                    C.MIN_MARKET_YES_LEAD_GAP_NORMAL,
                )
            ),
            4,
        ),
        "max_market_prob_for_buy": round(
            float(getattr(settings, "max_market_prob_for_buy", 0.75)), 4
        ),
        "forecast_gate_buy": bool(getattr(settings, "forecast_gate_buy", False)),
    }


def emit_mom_diag(
    *,
    market_id: str,
    event_id: Optional[str],
    title: str,
    gamma_probability: float,
    settings: RuntimeSettings,
    all_event_ids: List[str],
    dbl_signal_raw: bool,
    dbl_trigger: str,
    dbl_meta: Dict[str, Any],
    leader_meta_dbl: Dict[str, Any],
    mom_signal_raw: bool,
    mom_trigger: str,
    mom_meta: Dict[str, Any],
    leader_meta_mom: Dict[str, Any],
    is_double_momentum: bool,
    is_momentum_entry: bool,
) -> None:
    """emit one [mom_diag] json line per entry eval — grep-friendly.

    captures effective bands, both leader-window check outputs, and per-sibling
    sample stats inside the 15m window so we can see whether a sibling
    was silently dropped due to insufficient samples.
    """
    now_ts = time.time()
    siblings_info: List[Dict[str, Any]] = []
    for mid in all_event_ids:
        pts = load_samples_for_market(mid, MOM_DIAG_SIBLING_WINDOW_SEC, now_ts)
        if pts:
            oldest_ts, oldest_yes = pts[0]
            newest_ts, newest_yes = pts[-1]
            siblings_info.append(
                {
                    "mid": mid,
                    "n": len(pts),
                    "oldest_ts": round(float(oldest_ts), 2),
                    "newest_ts": round(float(newest_ts), 2),
                    "oldest_yes": round(float(oldest_yes), 4),
                    "newest_yes": round(float(newest_yes), 4),
                    "age_oldest_sec": round(now_ts - float(oldest_ts), 1),
                }
            )
        else:
            siblings_info.append(
                {
                    "mid": mid,
                    "n": 0,
                    "oldest_ts": 0.0,
                    "newest_ts": 0.0,
                    "oldest_yes": 0.0,
                    "newest_yes": 0.0,
                    "age_oldest_sec": 0.0,
                }
            )

    payload = {
        "market_id": market_id,
        "event_id": event_id or "",
        "title": str(title)[:120],
        "gamma_probability": round(float(gamma_probability), 4),
        "double_momentum_min_price": round(
            float(getattr(settings, "double_momentum_min_price", C.DOUBLE_MOMENTUM_MIN_PRICE)),
            4,
        ),
        "double_momentum_max_price": round(
            float(getattr(settings, "double_momentum_max_price", C.DOUBLE_MOMENTUM_MAX_PRICE)),
            4,
        ),
        "momentum_min_price": round(
            float(getattr(settings, "momentum_min_price", C.MOMENTUM_MIN_PRICE)), 4
        ),
        "momentum_max_entry": round(
            float(getattr(settings, "momentum_max_entry", C.MOMENTUM_MAX_ENTRY)), 4
        ),
        "buy_min": round(float(getattr(settings, "buy_min", 0.80)), 4),
        "buy_max": round(float(getattr(settings, "buy_max", 0.84)), 4),
        "leader_yield_fall_min_abs_pts": round(
            float(getattr(settings, "leader_yield_fall_min_abs_pts", C.LEADER_YIELD_FALL_MIN_ABS_PTS)),
            4,
        ),
        "leader_yield_fall_min_frac": round(
            float(getattr(settings, "leader_yield_fall_min_frac", C.LEADER_YIELD_FALL_MIN_FRAC)),
            4,
        ),
        "leader_yield_min_leader_old_price": round(
            float(getattr(settings, "leader_yield_min_leader_old_price", C.LEADER_YIELD_MIN_LEADER_OLD_PRICE)),
            4,
        ),
        "collective_fall_min_abs_pts": round(
            float(getattr(settings, "collective_fall_min_abs_pts", C.COLLECTIVE_FALL_MIN_ABS_PTS)),
            4,
        ),
        "collective_fall_min_frac": round(
            float(getattr(settings, "collective_fall_min_frac", C.COLLECTIVE_FALL_MIN_FRAC)),
            4,
        ),
        "min_samples": int(C.MOMENTUM_MIN_SAMPLE_POINTS),
        "dbl": {
            "signal_raw": bool(dbl_signal_raw),
            "trigger": str(dbl_trigger),
            "window_sec": float(dbl_meta.get("std_window_sec") or 0.0),
            "abs_rise": round(float(dbl_meta.get("std_abs_rise") or 0.0), 4),
            "pct_rise": round(float(dbl_meta.get("std_pct_rise") or 0.0), 4),
            "old_price": round(float(dbl_meta.get("std_old_price") or 0.0), 4),
            "new_price": round(float(dbl_meta.get("std_new_price") or 0.0), 4),
        },
        "leader_dbl": {
            "leader_id": str(leader_meta_dbl.get("leader_id") or ""),
            "leader_old": round(float(leader_meta_dbl.get("leader_old") or 0.0), 4),
            "leader_new": round(float(leader_meta_dbl.get("leader_new") or 0.0), 4),
            "drop_pts": round(float(leader_meta_dbl.get("drop_pts") or 0.0), 4),
            "drop_frac": round(float(leader_meta_dbl.get("drop_frac") or 0.0), 4),
            "reason": str(leader_meta_dbl.get("reason") or ""),
            "parsed_window_sec": float(leader_meta_dbl.get("parsed_window_sec") or 0.0),
            "collective_total_drop_pts": round(
                float(leader_meta_dbl.get("collective_total_drop_pts") or 0.0), 4
            ),
            "collective_drop_frac": round(float(leader_meta_dbl.get("collective_drop_frac") or 0.0), 4),
            "pass_condition": str(leader_meta_dbl.get("pass_condition") or ""),
        },
        "mom": {
            "signal_raw": bool(mom_signal_raw),
            "trigger": str(mom_trigger),
            "window_sec": float(mom_meta.get("std_window_sec") or 0.0),
            "abs_rise": round(float(mom_meta.get("std_abs_rise") or 0.0), 4),
            "pct_rise": round(float(mom_meta.get("std_pct_rise") or 0.0), 4),
            "old_price": round(float(mom_meta.get("std_old_price") or 0.0), 4),
            "new_price": round(float(mom_meta.get("std_new_price") or 0.0), 4),
        },
        "leader_mom": {
            "leader_id": str(leader_meta_mom.get("leader_id") or ""),
            "leader_old": round(float(leader_meta_mom.get("leader_old") or 0.0), 4),
            "leader_new": round(float(leader_meta_mom.get("leader_new") or 0.0), 4),
            "drop_pts": round(float(leader_meta_mom.get("drop_pts") or 0.0), 4),
            "drop_frac": round(float(leader_meta_mom.get("drop_frac") or 0.0), 4),
            "reason": str(leader_meta_mom.get("reason") or ""),
            "parsed_window_sec": float(leader_meta_mom.get("parsed_window_sec") or 0.0),
            "collective_total_drop_pts": round(
                float(leader_meta_mom.get("collective_total_drop_pts") or 0.0), 4
            ),
            "collective_drop_frac": round(float(leader_meta_mom.get("collective_drop_frac") or 0.0), 4),
            "pass_condition": str(leader_meta_mom.get("pass_condition") or ""),
        },
        "is_double_momentum": bool(is_double_momentum),
        "is_momentum_entry": bool(is_momentum_entry),
        "n_event_market_ids": len(all_event_ids),
        "siblings": siblings_info,
        "effective_settings_dump": effective_settings_dump_for_momentum_eval(settings),
    }
    try:
        s = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = repr(payload)
    print(f"[mom_diag] {s}")


def _momentum_eval_debug_line(payload: Dict[str, Any]) -> None:
    if not C.MOMENTUM_DECISION_DEBUG_LOG:
        return
    try:
        s = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = repr(payload)
    print(term_wrap(TERM_DIM, f"[momentum_eval] {s}"))


def detect_momentum_switch(
    target_market: Dict[str, Any],
    target_mid: str,
    target_yes: float,
    state: Dict[str, Any],
    client: PolymarketClient,
    event_cache: Dict[str, List[Dict[str, Any]]],
    settings: RuntimeSettings,
) -> Optional[Tuple[Dict[str, Any], str]]:
    """
    if we hold another bucket in the same event and this scanned bucket shows entry-style
    momentum, is in the momentum price band, and YES >= held_yes + gap,
    return (held_market_dict, held_state_key) so the runner can sell held then BUY here.

    returns:
    - None when no rotation applies.
    """
    ev_id, ev_mids = collect_event_id_and_market_ids(target_market, event_cache, client)
    if not ev_id or not ev_mids:
        return None
    if event_buy_cooldown_active(state, ev_id):
        return None
    active = state.get("active_trades") or {}
    if not isinstance(active, dict):
        return None

    held_key: Optional[str] = None
    held_row: Optional[Dict[str, Any]] = None
    for ak, row in active.items():
        if not isinstance(row, dict):
            continue
        smid = str(row.get("market_id") or "").strip() or str(ak).strip()
        if smid in ev_mids and smid != target_mid:
            if held_key is not None:
                return None
            held_key = str(ak).strip()
            held_row = row
    if not held_key or not held_row:
        return None

    if ev_id not in event_cache:
        event_cache[ev_id] = client.get_markets_for_event_id(ev_id)
    siblings = list(event_cache.get(ev_id) or [])
    if not siblings:
        return None

    wsec = momentum_window_sec(settings)
    rise_thr = momentum_entry_rise_thr(settings)
    mom_signal, _ = momentum_entry_signal(
        target_mid,
        rise_threshold=rise_thr,
        window_sec=wsec,
    )
    if not mom_signal:
        return None

    mom_min = float(getattr(settings, "momentum_min_price", C.MOMENTUM_MIN_PRICE))
    mom_max = float(getattr(settings, "momentum_max_entry", C.MOMENTUM_MAX_ENTRY))
    if not (mom_min - 1e-12 <= target_yes <= mom_max + 1e-12):
        return None

    held_yes = float(held_row.get("last_price") or held_row.get("entry_price") or 0.0)
    gap = float(C.MOMENTUM_SWITCH_ABOVE_HELD_GAP)
    if target_yes + 1e-12 < held_yes + gap:
        return None

    held_mid = str(held_row.get("market_id") or "").strip() or held_key
    held_market: Optional[Dict[str, Any]] = None
    for m in siblings:
        if isinstance(m, dict) and str(m.get("id") or "").strip() == held_mid:
            held_market = m
            break
    if held_market is None:
        return None
    return (held_market, held_key)


def momentum_competitor_dominates_held_exit(
    market: Dict[str, Any],
    market_id: str,
    held_mark_yes: float,
    settings: RuntimeSettings,
    event_cache: Dict[str, List[Dict[str, Any]]],
    client: PolymarketClient,
) -> Tuple[Optional[str], Optional[float]]:
    """
    sell held when another sibling bucket has entry-style momentum, is inside the momentum
    price band, and leads us by at least MOMENTUM_SWITCH_ABOVE_HELD_GAP (best YES wins ties).

    returns:
    - (reason, ref_gamma_yes) or (None, None).
    """
    ev_id, _ = collect_event_id_and_market_ids(market, event_cache, client)
    if not ev_id:
        return None, None
    if ev_id not in event_cache:
        event_cache[ev_id] = client.get_markets_for_event_id(ev_id)
    siblings = list(event_cache.get(ev_id) or [])
    if len(siblings) < 2:
        return None, None

    wsec = momentum_window_sec(settings)
    rise_thr = momentum_entry_rise_thr(settings)
    mom_min = float(getattr(settings, "momentum_min_price", C.MOMENTUM_MIN_PRICE))
    mom_max = float(getattr(settings, "momentum_max_entry", C.MOMENTUM_MAX_ENTRY))
    gap = float(C.MOMENTUM_SWITCH_ABOVE_HELD_GAP)

    candidates: List[Tuple[float, str]] = []
    for m in siblings:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid or mid == market_id:
            continue
        yes = float(parse_market_probability(m))
        if yes + 1e-12 < held_mark_yes + gap:
            continue
        if not (mom_min - 1e-12 <= yes <= mom_max + 1e-12):
            continue
        sig, _ = momentum_entry_signal(
            mid,
            rise_threshold=rise_thr,
            window_sec=wsec,
        )
        if not sig:
            continue
        candidates.append((yes, mid))

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: -x[0])
    gamma_prob = parse_market_probability(market)
    return "momentum-competitor-dominant", float(gamma_prob)


def peer_market_ids_excluding_self(
    client: PolymarketClient,
    market: Dict[str, Any],
    event_cache: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    eids = gamma_event_ids_for_market(market)
    if not eids:
        return []
    eid = eids[0]
    if eid not in event_cache:
        event_cache[eid] = client.get_markets_for_event_id(eid)
    mid = str(market.get("id") or "").strip()
    out: List[str] = []
    for m in event_cache.get(eid) or []:
        sm = str(m.get("id") or "").strip()
        if sm and sm != mid:
            out.append(sm)
    return out


def evaluate_entry(
    parsed: ParsedTempMarket,
    consensus_c: float,
    market_yes: float,
    market: Dict[str, Any],
    client: PolymarketClient,
    settings: RuntimeSettings,
    state: Dict[str, Any],
    event_cache: Dict[str, List[Dict[str, Any]]],
) -> TradeDecision:
    """
    strict entry evaluation — ALL conditions must pass for BUY.

    params:
    - parsed: parsed temperature market title.
    - consensus_c: bias-adjusted forecast °C.
    - market_yes: CLOB YES price (0-1).
    - market: full gamma market dict.
    - client: polymarket client for event lookups.
    - settings: runtime settings.
    - state: bot state.
    - event_cache: shared event market cache.

    returns:
    - TradeDecision with BUY or SKIP.
    """
    city = parsed.city_key
    date_iso = (
        parsed.event_date.isoformat() if isinstance(parsed.event_date, date) else ""
    )
    bucket = (parsed.raw_title or "")[:160]
    market_id = str(market.get("id") or "").strip()

    sigma = resolved_research_sigma_c(float(settings.research_sigma_c), city)
    model_p = compute_model_prob(parsed, consensus_c, settings.research_sigma_c)
    mkt = float(market_yes)
    edge = bucket_edge(model_p, mkt)

    ev_id, ev_mids = collect_event_id_and_market_ids(market, event_cache, client)
    siblings: List[Dict[str, Any]] = []
    if ev_id:
        if ev_id not in event_cache:
            event_cache[ev_id] = client.get_markets_for_event_id(ev_id)
        siblings = list(event_cache.get(ev_id) or [])

    wsec = momentum_window_sec(settings)
    rank = yes_rank_by_market_prob(market_id, siblings) if siblings else 1
    peer_ids = sorted({str(x).strip() for x in ev_mids if str(x).strip()})
    top_changes = top_price_change_peers(peer_ids, window_sec=wsec, top_n=3)

    min_lead_mkt = float(
        getattr(settings, "min_lead_over_runner_up", C.MIN_LEAD_OVER_RUNNER_UP)
    )
    market_lead_gap, runner_up_yes = (
        market_yes_lead_gap_vs_runner_up(market_id, mkt, siblings)
        if siblings
        else (1.0, 0.0)
    )

    rise_thr = momentum_entry_rise_thr(settings)
    rise_pct_thr = momentum_entry_pct_rise_thr(settings)
    mom_start_min = float(
        getattr(settings, "momentum_min_start_price", C.MOMENTUM_MIN_START_PRICE)
    )
    mom_min = float(getattr(settings, "momentum_min_price", C.MOMENTUM_MIN_PRICE))
    mom_max = float(getattr(settings, "momentum_max_entry", C.MOMENTUM_MAX_ENTRY))
    dbl_rise_thr = float(
        getattr(settings, "double_momentum_entry_rise", C.DOUBLE_MOMENTUM_ENTRY_RISE)
    )
    dbl_rise_pct_thr = double_momentum_entry_pct_rise_thr(settings)
    dbl_start_min = float(
        getattr(
            settings,
            "double_momentum_min_start_price",
            C.DOUBLE_MOMENTUM_MIN_START_PRICE,
        )
    )
    dbl_min = float(
        getattr(settings, "double_momentum_min_price", C.DOUBLE_MOMENTUM_MIN_PRICE)
    )
    dbl_max = float(
        getattr(settings, "double_momentum_max_price", C.DOUBLE_MOMENTUM_MAX_PRICE)
    )

    all_event_ids = sorted({str(x).strip() for x in ev_mids if str(x).strip()})
    min_leader_old = float(
        getattr(
            settings,
            "leader_yield_min_leader_old_price",
            C.LEADER_YIELD_MIN_LEADER_OLD_PRICE,
        )
    )
    leader_fall_abs = float(
        getattr(
            settings, "leader_yield_fall_min_abs_pts", C.LEADER_YIELD_FALL_MIN_ABS_PTS
        )
    )
    leader_fall_frac = float(
        getattr(settings, "leader_yield_fall_min_frac", C.LEADER_YIELD_FALL_MIN_FRAC)
    )
    coll_abs = float(
        getattr(settings, "collective_fall_min_abs_pts", C.COLLECTIVE_FALL_MIN_ABS_PTS)
    )
    coll_frac = float(
        getattr(settings, "collective_fall_min_frac", C.COLLECTIVE_FALL_MIN_FRAC)
    )

    entry_windows = momentum_entry_candidate_windows(settings)
    dbl_signal_raw, dbl_trigger, dbl_meta, leader_meta_dbl = momentum_leader_same_window_check(
        market_id=market_id,
        event_market_ids=list(all_event_ids),
        windows_sec=entry_windows,
        abs_rise_threshold=dbl_rise_thr,
        pct_rise_threshold=dbl_rise_pct_thr,
        min_start_price=dbl_start_min,
        min_current_price=dbl_min,
        max_current_price=dbl_max,
        min_leader_old_price=min_leader_old,
        min_fall_abs_pts=leader_fall_abs,
        min_fall_frac_of_old=leader_fall_frac,
        min_samples=C.MOMENTUM_MIN_SAMPLE_POINTS,
        collective_fall_min_abs_pts=coll_abs,
        collective_fall_min_frac=coll_frac,
    )
    mom_signal_raw, mom_trigger, mom_meta, leader_meta_mom = momentum_leader_same_window_check(
        market_id=market_id,
        event_market_ids=list(all_event_ids),
        windows_sec=entry_windows,
        abs_rise_threshold=rise_thr,
        pct_rise_threshold=rise_pct_thr,
        min_start_price=mom_start_min,
        min_current_price=mom_min,
        max_current_price=mom_max,
        min_leader_old_price=min_leader_old,
        min_fall_abs_pts=leader_fall_abs,
        min_fall_frac_of_old=leader_fall_frac,
        min_samples=C.MOMENTUM_MIN_SAMPLE_POINTS,
        collective_fall_min_abs_pts=coll_abs,
        collective_fall_min_frac=coll_frac,
    )

    dbl_rise_only, _, _ = momentum_multi_window_check(
        market_id=market_id,
        abs_rise_threshold=dbl_rise_thr,
        pct_rise_threshold=dbl_rise_pct_thr,
        windows_sec=entry_windows,
        min_start_price=dbl_start_min,
        min_current_price=dbl_min,
        max_current_price=dbl_max,
    )
    mom_rise_only, _, _ = momentum_multi_window_check(
        market_id=market_id,
        abs_rise_threshold=rise_thr,
        pct_rise_threshold=rise_pct_thr,
        windows_sec=entry_windows,
        min_start_price=mom_start_min,
        min_current_price=mom_min,
        max_current_price=mom_max,
    )

    is_double_momentum = bool(dbl_signal_raw)
    is_momentum_from_std = bool(mom_signal_raw and not is_double_momentum)
    active_leader_meta: Dict[str, Any] = {}
    leader_yield_win_sec = 0.0
    if is_double_momentum:
        active_leader_meta = dict(leader_meta_dbl)
        leader_yield_win_sec = float(
            leader_meta_dbl.get("leader_eval_window_sec")
            or parse_trigger_window_seconds(dbl_trigger)
            or 0.0
        )
    elif is_momentum_from_std:
        active_leader_meta = dict(leader_meta_mom)
        leader_yield_win_sec = float(
            leader_meta_mom.get("leader_eval_window_sec")
            or parse_trigger_window_seconds(mom_trigger)
            or 0.0
        )

    # standard momentum: abs + pct in tighter band + ex-leader must bleed same window.
    is_momentum_entry = bool(is_double_momentum or is_momentum_from_std)

    # surgical diagnostic — single line, grep-friendly. logs every entry eval so
    # we can see *which* gate failed when an obvious surge gets skipped.
    emit_mom_diag(
        market_id=market_id,
        event_id=ev_id,
        title=bucket,
        gamma_probability=mkt,
        settings=settings,
        all_event_ids=all_event_ids,
        dbl_signal_raw=dbl_signal_raw,
        dbl_trigger=dbl_trigger,
        dbl_meta=dbl_meta,
        leader_meta_dbl=leader_meta_dbl,
        mom_signal_raw=mom_signal_raw,
        mom_trigger=mom_trigger,
        mom_meta=mom_meta,
        leader_meta_mom=leader_meta_mom,
        is_double_momentum=is_double_momentum,
        is_momentum_entry=is_momentum_entry,
    )

    # mom_meta / dbl_meta below carry legacy std-window fields used by the
    # debug payload + finish() trigger metric extraction.

    _, _, momentum_15m = absolute_price_change_in_window(
        market_id,
        wsec,
        min_samples=C.MOMENTUM_MIN_SAMPLE_POINTS,
    )

    def _resolve_entry_type() -> str:
        if is_double_momentum:
            return "double_momentum"
        if is_momentum_entry:
            return "momentum"
        return "normal"

    def finish(
        decision: str,
        reason: str,
        competition: Optional[CompetitionResult] = None,
        research: Optional[ResearchEdgeDecision] = None,
        *,
        momentum_relaxed: bool = False,
    ) -> TradeDecision:
        relaxed = bool(momentum_relaxed and decision == "BUY")
        etype = _resolve_entry_type() if decision == "BUY" else "normal"
        # pick which window's metrics to expose: prefer the path that triggered.
        if etype == "double_momentum":
            tr_window = dbl_trigger
            tr_abs = dbl_meta.get("std_abs_rise", 0.0)
            tr_pct = dbl_meta.get("std_pct_rise", 0.0)
            tr_old = dbl_meta.get("std_old_price", 0.0)
            tr_new = dbl_meta.get("std_new_price", 0.0)
        elif etype == "momentum":
            tr_window = mom_trigger
            tr_abs = mom_meta.get("std_abs_rise", 0.0)
            tr_pct = mom_meta.get("std_pct_rise", 0.0)
            tr_old = mom_meta.get("std_old_price", 0.0)
            tr_new = mom_meta.get("std_new_price", 0.0)
        else:
            tr_window = "none"
            tr_abs = 0.0
            tr_pct = 0.0
            tr_old = mom_meta.get("std_old_price", 0.0)
            tr_new = mom_meta.get("std_new_price", 0.0)
        lf_id = ""
        lf_title = ""
        lf_old_y = lf_new_y = lf_drop = lf_drop_frac = lwsec_td = 0.0
        if decision == "BUY" and etype in ("momentum", "double_momentum"):
            lf_id = str(active_leader_meta.get("leader_id") or "").strip()
            lf_title = market_title_by_id(siblings, lf_id) if siblings else lf_id
            lf_old_y = float(active_leader_meta.get("leader_old") or 0.0)
            lf_new_y = float(active_leader_meta.get("leader_new") or 0.0)
            lf_drop = float(active_leader_meta.get("drop_pts") or 0.0)
            lf_drop_frac = float(active_leader_meta.get("drop_frac") or 0.0)
            lwsec_td = float(leader_yield_win_sec or 0.0)
        td = TradeDecision(
            city=city,
            date_iso=date_iso,
            chosen_bucket=bucket,
            model_prob=float(model_p),
            market_prob=mkt,
            edge=float(edge),
            consensus_c=float(consensus_c),
            sigma_used=float(sigma),
            decision=decision,
            reason=reason,
            momentum_15m=float(momentum_15m),
            competition=competition,
            research=research,
            momentum_relaxed_gates=relaxed,
            event_yes_rank=int(rank),
            entry_type=etype,
            trigger_window=str(tr_window),
            trigger_abs_rise=float(tr_abs),
            trigger_pct_rise=float(tr_pct),
            trigger_old_price=float(tr_old),
            trigger_new_price=float(tr_new),
            leader_fallen_market_id=lf_id[:64],
            leader_fallen_market_title=str(lf_title)[:220],
            leader_fallen_old_yes=float(lf_old_y),
            leader_fallen_new_yes=float(lf_new_y),
            leader_fallen_drop_pts=float(lf_drop),
            leader_fallen_drop_frac=float(lf_drop_frac),
            leader_yield_window_sec=float(lwsec_td),
        )
        _log_decision(td)
        _momentum_eval_debug_line(
            {
                "market_id": market_id,
                "city": city,
                "momentum_15m_points": round(float(momentum_15m), 4),
                "mom_trigger_window": mom_trigger,
                "mom_std_pass": mom_meta.get("std_passed", False),
                "mom_std_rise_points": round(
                    float(mom_meta.get("std_abs_rise", 0.0)), 4
                ),
                "mom_std_rise_pct": round(float(mom_meta.get("std_pct_rise", 0.0)), 4),
                "double_trigger_window": dbl_trigger,
                "dbl_std_pass": dbl_meta.get("std_passed", False),
                "dbl_std_rise_points": round(
                    float(dbl_meta.get("std_abs_rise", 0.0)), 4
                ),
                "dbl_std_rise_pct": round(float(dbl_meta.get("std_pct_rise", 0.0)), 4),
                "yes_rank": int(rank),
                "market_lead_gap": round(float(market_lead_gap), 4),
                "runner_up_yes": round(float(runner_up_yes), 4),
                "min_lead_required": round(float(min_lead_mkt), 4),
                "top_peers_15m_points": [
                    {"id": pid, "chg_points": round(float(ch), 4)}
                    for pid, ch in top_changes
                ],
                "decision": decision,
                "reason": reason[:160],
                "momentum_relaxed_buy": relaxed,
                "entry_type": etype,
                "is_double_momentum": is_double_momentum,
                "leader_yield_window_sec": round(float(leader_yield_win_sec), 2),
                "leader_yield_reason": str(active_leader_meta.get("reason") or ""),
                "active_trigger_window": td.trigger_window,
                "active_abs_rise": round(float(td.trigger_abs_rise), 4),
                "active_pct_rise": round(float(td.trigger_pct_rise), 4),
            }
        )
        return td

    # 0. event-level churn — block after repeated losses on same event
    from strategy.churn import (
        churn_event_allows_buy,
        churn_event_recent_stoploss_active,
        churn_event_unstable_active,
    )

    if ev_id and not churn_event_allows_buy(state, ev_id):
        return finish("SKIP", f"event_churn_cooldown active for {ev_id}")
    if ev_id and churn_event_recent_stoploss_active(state, ev_id):
        return finish("SKIP", f"event_recent_stoploss cooldown active for {ev_id}")
    if ev_id and churn_event_unstable_active(state, ev_id):
        return finish("SKIP", f"event_unstable cooldown active for {ev_id}")

    # 1. event cooldown
    if ev_id and event_buy_cooldown_active(state, ev_id):
        return finish("SKIP", f"event_buy_cooldown active for {ev_id}")

    # 2. duplicate check — already holding this market
    active = state.get("active_trades") or {}
    if not isinstance(active, dict):
        active = {}
    if market_id in active:
        return finish("SKIP", "already_holding_this_market")

    # 3. max positions per event
    max_pos = int(getattr(settings, "max_positions_per_event", 1))
    if max_pos > 0 and ev_mids:
        open_n = 0
        for ak, row in active.items():
            if not isinstance(row, dict):
                continue
            am = str(row.get("market_id") or ak).strip()
            if am in ev_mids:
                open_n += 1
        if open_n >= max_pos:
            return finish(
                "SKIP", f"max_positions_per_event open={open_n} max={max_pos}"
            )

    if (dbl_rise_only or mom_rise_only) and not is_momentum_entry:
        lk = ""
        if dbl_rise_only:
            lk = str(leader_meta_dbl.get("reason") or "")
        elif mom_rise_only:
            lk = str(leader_meta_mom.get("reason") or "")
        if len(all_event_ids) < 2:
            skip_lk = "leader_yield_blocked_single_bucket_event"
        else:
            skip_lk = f"leader_yield_blocked:{lk}" if lk else "leader_yield_blocked"
        return finish("SKIP", skip_lk)

    # 4–6 model path gates (skipped when momentum path qualifies)
    if not is_momentum_entry:
        max_mkt = float(getattr(settings, "max_market_prob_for_buy", 0.75))
        if mkt > max_mkt + 1e-9:
            return finish("SKIP", f"market_prob {mkt:.4f} > max {max_mkt:.4f}")

        min_model = float(getattr(settings, "min_model_prob_for_buy", 0.10))
        if model_p + 1e-9 < min_model:
            return finish("SKIP", f"model_prob {model_p:.4f} < min {min_model:.4f}")

        peak_min = float(getattr(settings, "decision_min_model_peak_prob", 0.12))
        if model_p + 1e-9 < peak_min:
            return finish("SKIP", f"model_flat {model_p:.4f} < peak_min {peak_min:.4f}")

    # 7. competition — normal BUY only: live YES lead vs runner-up (momentum bypasses).
    # evaluate_competition still fills TradeDecision metadata (model vs market method).
    comp = evaluate_competition(
        client,
        market,
        mkt,
        float(settings.min_lead_over_runner_up),
        event_cache,
        consensus_c=consensus_c,
        sigma_c_setting=float(settings.research_sigma_c),
    )
    if settings.enable_competition_filter and not is_momentum_entry:
        min_mkt_gap = float(
            getattr(
                settings,
                "min_market_yes_lead_gap_normal",
                C.MIN_MARKET_YES_LEAD_GAP_NORMAL,
            )
        )
        if market_lead_gap + 1e-9 < min_mkt_gap:
            return finish(
                "SKIP",
                "competition_fail "
                f"market_gap={market_lead_gap:.4f} < min_market_yes_lead_gap_normal={min_mkt_gap:.4f}",
                competition=comp,
            )

    # 9. negative momentum — don't buy into a falling bucket
    if momentum_15m < -0.10 and not is_momentum_entry:
        return finish(
            "SKIP",
            f"negative_momentum {momentum_15m:+.3f} points in 15m",
            competition=comp,
        )

    # 10. research edge gate (fee-aware) — skipped for momentum entries
    rd = research_edge_decision(
        parsed,
        float(consensus_c),
        mkt,
        sigma_c=float(sigma),
        min_edge=float(settings.research_min_edge),
        min_edge_after_fees_add=float(settings.research_min_edge_after_fees_add),
        fee_rate_for_drag=float(settings.research_weather_taker_fee_rate),
        gate_enabled=bool(settings.research_edge_gate_buy),
        soft_match_enabled=bool(settings.research_crowd_soft_match),
        soft_band=float(settings.research_crowd_soft_band),
        soft_edge_factor=float(settings.research_crowd_soft_edge_factor),
        disagree_extra_edge=float(settings.research_crowd_disagree_extra_edge),
        disagree_gap=float(settings.research_crowd_disagree_gap),
        implied_soft_floor=float(settings.research_edge_implied_soft_floor),
        implied_soft_boost_mult=float(settings.research_edge_implied_soft_boost),
    )
    if not rd.passes_gate and not is_momentum_entry:
        return finish(
            "SKIP",
            rd.skip_reason or "research_edge_fail",
            competition=comp,
            research=rd,
        )

    reason = (
        "momentum_entry_ride_the_wave" if is_momentum_entry else "passed_all_filters"
    )
    return finish(
        "BUY",
        reason,
        competition=comp,
        research=rd,
        momentum_relaxed=is_momentum_entry,
    )


def stop_loss_bar_for_entry_type(
    entry_type: str,
    settings: RuntimeSettings,
) -> float:
    """Return the stop-loss mark bar based on the entry type stored at buy time."""
    et = str(entry_type or "normal").strip()
    if et == "double_momentum":
        return float(
            getattr(settings, "stop_loss_double_momentum", C.STOP_LOSS_DOUBLE_MOMENTUM)
        )
    if et == "momentum":
        return float(getattr(settings, "stop_loss_momentum", C.STOP_LOSS_MOMENTUM))
    if et in ("manual", "ui"):
        return float(getattr(settings, "stop_loss_manual", C.STOP_LOSS_MANUAL))
    return float(getattr(settings, "stop_loss_normal", C.STOP_LOSS_NORMAL))


def stop_loss_entry_drop_pct_for_entry_type(
    entry_type: str,
    settings: RuntimeSettings,
) -> float:
    et = str(entry_type or "normal").strip()
    if et == "double_momentum":
        return float(
            getattr(
                settings,
                "stop_loss_double_momentum_entry_drop_pct",
                C.STOP_LOSS_DOUBLE_MOMENTUM_ENTRY_DROP_PCT,
            )
        )
    if et == "momentum":
        return float(
            getattr(
                settings,
                "stop_loss_momentum_entry_drop_pct",
                C.STOP_LOSS_MOMENTUM_ENTRY_DROP_PCT,
            )
        )
    if et in ("manual", "ui"):
        return float(
            getattr(
                settings,
                "stop_loss_manual_entry_drop_pct",
                C.STOP_LOSS_MANUAL_ENTRY_DROP_PCT,
            )
        )
    return float(
        getattr(
            settings,
            "stop_loss_normal_entry_drop_pct",
            C.STOP_LOSS_NORMAL_ENTRY_DROP_PCT,
        )
    )


def trailing_stop_level(
    entry_price: float,
    highest_seen_price: float,
    settings: RuntimeSettings,
) -> Optional[float]:
    """compute trailing stop level (or None if not yet activated).

    activation: highest_seen_price >= entry_price + activation_gain.
    locked level: entry_price + lock_gain.
    """
    if not bool(getattr(settings, "trailing_stop_enabled", True)):
        return None
    entry = float(entry_price or 0.0)
    peak = float(highest_seen_price or 0.0)
    if entry <= 1e-9 or peak <= 1e-9:
        return None
    act = float(getattr(settings, "trailing_stop_activation_gain", 0.20))
    lock = float(getattr(settings, "trailing_stop_lock_gain", 0.10))
    if peak + 1e-12 < entry + act:
        return None
    return entry + lock


def effective_stop_price_for_trade(
    entry_type: str,
    entry_price: float,
    settings: RuntimeSettings,
    *,
    highest_seen_price: float = 0.0,
) -> float:
    """compute the effective stop level combining floor / entry-drop / trailing.

    keeping the call signature backward-compatible: callers that don't pass
    highest_seen_price get the legacy floor + entry-drop only.
    """
    hard_floor = stop_loss_bar_for_entry_type(entry_type, settings)
    drop_pct = stop_loss_entry_drop_pct_for_entry_type(entry_type, settings)
    entry_relative = max(0.0, float(entry_price) * (1.0 - float(drop_pct)))
    base = max(float(hard_floor), float(entry_relative))
    trail = trailing_stop_level(entry_price, highest_seen_price, settings)
    if trail is None:
        return base
    return max(base, float(trail))


def classify_stop_loss_breach(
    entry_type: str,
    entry_price: float,
    live_price: float,
    settings: RuntimeSettings,
    *,
    highest_seen_price: float = 0.0,
) -> Optional[Tuple[str, float]]:
    """label which stop-loss component breached, if any.

    compares live_price to the same combined bar as `effective_stop_price_for_trade`
    and attributes the breach to whichever component pins that bar (trailing wins
    ties, then entry-relative, then hard floor).
    """
    px = float(live_price or 0.0)
    if px <= 0:
        return None
    hard_floor = stop_loss_bar_for_entry_type(entry_type, settings)
    drop_pct = stop_loss_entry_drop_pct_for_entry_type(entry_type, settings)
    entry_relative = max(0.0, float(entry_price) * (1.0 - float(drop_pct)))
    trail = trailing_stop_level(entry_price, highest_seen_price, settings)
    effective = effective_stop_price_for_trade(
        entry_type,
        float(entry_price),
        settings,
        highest_seen_price=float(highest_seen_price or 0.0),
    )
    if px + 1e-12 >= float(effective):
        return None
    # which single component set `effective`?
    if trail is not None and float(effective) <= float(trail) + 1e-9:
        return (C.SL_CATEGORY_TRAILING, float(trail))
    if (
        float(effective) <= float(entry_relative) + 1e-9
        and entry_relative >= hard_floor - 1e-9
    ):
        return (C.SL_CATEGORY_RELATIVE, float(entry_relative))
    if float(effective) <= float(hard_floor) + 1e-9:
        return (C.SL_CATEGORY_ABSOLUTE, float(hard_floor))
    return (C.SL_CATEGORY_RELATIVE, float(effective))


def check_exits(
    market: Dict[str, Any],
    trade: Dict[str, Any],
    market_id: str,
    settings: RuntimeSettings,
    event_cache: Dict[str, List[Dict[str, Any]]],
    client: PolymarketClient,
) -> Tuple[Optional[str], Optional[float]]:
    """
    Check all exit conditions for an open position.

    Returns (exit_reason, reference_price) or (None, None) if HOLD.

    Exit priority:
    1. Momentum fast exit — absolute 0.15 price drop from peak in 15 min window
    2. Competitor dominance / surge
    3. Time-decay exit
    """
    gamma_prob = parse_market_probability(market)
    mark = float(trade.get("last_price") or gamma_prob)
    entry = float(trade.get("entry_price") or 0)
    entry_time_utc = str(trade.get("entry_time_utc") or "").strip()

    # 1. fast stop-loss: absolute drop from rolling peak — since entry if known,
    # otherwise legacy window anchored to earliest sample (e.g. manual trades).
    wsec = momentum_window_sec(settings)
    fast_exit = False
    dd = 0.0
    entry_ts = 0.0
    if entry_time_utc:
        try:
            entry_ts = datetime.fromisoformat(entry_time_utc).timestamp()
        except (ValueError, TypeError):
            entry_ts = 0.0
    drop_thr = momentum_fast_exit_drop(settings)
    if entry_ts > 0:
        fast_exit, dd = should_fast_exit_since_ts(
            market_id,
            since_ts=entry_ts,
            drop_threshold=drop_thr,
            window_sec=wsec,
        )
    else:
        fast_exit, dd = should_fast_exit(
            market_id,
            drop_threshold=drop_thr,
            window_sec=wsec,
        )
    if fast_exit and mark < entry:
        # tag SL category so close_position can label it in logs/telegram.
        trade["sl_category"] = C.SL_CATEGORY_MOMENTUM
        trade["sl_drawdown_points"] = round(float(dd), 6)
        return "momentum-stop-loss", gamma_prob

    crash_thr = float(getattr(settings, "crash_drop_pct_from_peak", 0.0))
    crash_grace = float(
        getattr(
            settings,
            "crash_from_peak_grace_sec_after_entry",
            C.CRASH_FROM_PEAK_GRACE_SEC_AFTER_ENTRY,
        )
    )
    if crash_thr > 1e-12 and entry > 1e-12:
        skip_crash_grace = False
        if crash_grace > 1e-12:
            age_sec = seconds_since_entry_iso(entry_time_utc)
            if age_sec is not None and age_sec + 1e-12 < crash_grace:
                skip_crash_grace = True
        if not skip_crash_grace:
            peak_px = float(trade.get("highest_seen_price") or 0.0)
            mark_now = float(trade.get("last_price") or gamma_prob)
            if peak_px > 1e-12 and mark_now > 1e-12 and mark_now + 1e-12 < entry:
                drop_pct_from_peak = (peak_px - mark_now) / peak_px
                if drop_pct_from_peak + 1e-12 >= crash_thr:
                    trade["sl_category"] = C.SL_CATEGORY_CRASH_PEAK
                    trade["sl_level"] = round(float(mark_now), 6)
                    trade["crash_drop_pct_from_peak"] = round(
                        float(drop_pct_from_peak), 6
                    )
                    return "stop-loss", gamma_prob

    # 2. trailing stop / per-type SL breach (live mark vs effective stop)
    entry_type = str(trade.get("entry_type") or "normal").strip()
    highest_seen = float(trade.get("highest_seen_price") or 0.0)
    breach = classify_stop_loss_breach(
        entry_type,
        entry,
        mark,
        settings,
        highest_seen_price=highest_seen,
    )
    if breach is not None:
        category, level = breach
        trade["sl_category"] = category
        trade["sl_level"] = round(float(level), 6)
        trade["sl_effective"] = round(
            float(
                effective_stop_price_for_trade(
                    entry_type,
                    entry,
                    settings,
                    highest_seen_price=highest_seen,
                )
            ),
            6,
        )
        if category == C.SL_CATEGORY_TRAILING:
            return "trailing-stop", gamma_prob
        return "stop-loss", gamma_prob

    dom_reason, dom_ref = momentum_competitor_dominates_held_exit(
        market,
        market_id,
        mark,
        settings,
        event_cache,
        client,
    )
    if dom_reason:
        return dom_reason, dom_ref

    # 2. competitor surge: sibling bucket +0.15 price points in 15 minutes
    peers = peer_market_ids_excluding_self(client, market, event_cache)
    if peers:
        surged, surge_mid, surge_pct = peer_surge_detected(
            peers,
            surge_threshold=momentum_competitor_surge_thr(settings),
            window_sec=wsec,
        )
        if surged:
            return "competitor-surge", gamma_prob

    # 3. time-decay exit
    decay_exit, decay_reason = should_time_decay_exit(
        trade,
        current_price=mark,
        decay_hours=float(getattr(settings, "time_decay_hours", C.TIME_DECAY_HOURS)),
        min_gain_points=float(
            getattr(settings, "time_decay_min_gain", C.TIME_DECAY_MIN_GAIN)
        ),
        max_price_for_decay=float(
            getattr(settings, "time_decay_max_price", C.TIME_DECAY_MAX_PRICE)
        ),
    )
    if decay_exit:
        return "time-decay", gamma_prob

    return None, None
