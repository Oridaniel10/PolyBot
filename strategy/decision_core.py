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

rotation when holding: only the event market-yes **#1** may trigger a switch, and its
YES must be **>= held_yes + MOMENTUM_SWITCH_ABOVE_HELD_GAP** plus momentum rise +
momentum price band (see detect_momentum_switch / momentum_competitor_dominates_held_exit).
"""

from __future__ import annotations

import collections
import json
import time
from dataclasses import dataclass
from datetime import date
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
    momentum_entry_signal,
    peer_surge_detected,
    price_change_in_window,
    should_fast_exit,
    top_price_change_peers,
    yes_rank_by_market_prob,
)
from strategy.probability import parse_market_probability
from strategy.probability_engine import bucket_edge, compute_model_prob
from strategy.research_signal import (
    ResearchEdgeDecision,
    research_edge_decision,
)
from strategy.time_filter import should_time_decay_exit

DECISION_LOG_MAX = 200
_decision_log: Deque[Dict[str, Any]] = collections.deque(maxlen=DECISION_LOG_MAX)


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
    if we hold another bucket in the same event and this bucket is #1 by market YES,
    rose >= entry-rise in 15m, is in the momentum price band, and YES >= held_yes + gap,
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

    mom_signal, _ = momentum_entry_signal(
        target_mid,
        rise_threshold=C.MOMENTUM_ENTRY_RISE,
        window_sec=C.MOMENTUM_WINDOW_SECONDS,
    )
    if not mom_signal:
        return None

    mom_min = float(getattr(settings, "momentum_min_price", C.MOMENTUM_MIN_PRICE))
    mom_max = float(getattr(settings, "momentum_max_entry", C.MOMENTUM_MAX_ENTRY))
    if not (mom_min - 1e-12 <= target_yes <= mom_max + 1e-12):
        return None

    rank = yes_rank_by_market_prob(target_mid, siblings)
    if rank != 1:
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
    sell held when another bucket is #1 by market YES, has entry-style momentum,
    is inside the momentum price band, and leads us by at least MOMENTUM_SWITCH_ABOVE_HELD_GAP.

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

    scored: List[Tuple[str, float, Dict[str, Any]]] = []
    for m in siblings:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        scored.append((mid, parse_market_probability(m), m))
    if not scored:
        return None, None
    scored.sort(key=lambda x: -x[1])
    leader_mid, leader_yes, _leader_m = scored[0]
    if leader_mid == market_id:
        return None, None

    mom_signal, _ = momentum_entry_signal(
        leader_mid,
        rise_threshold=C.MOMENTUM_ENTRY_RISE,
        window_sec=C.MOMENTUM_WINDOW_SECONDS,
    )
    if not mom_signal:
        return None, None

    mom_min = float(getattr(settings, "momentum_min_price", C.MOMENTUM_MIN_PRICE))
    mom_max = float(getattr(settings, "momentum_max_entry", C.MOMENTUM_MAX_ENTRY))
    if not (mom_min - 1e-12 <= leader_yes <= mom_max + 1e-12):
        return None, None

    gap = float(C.MOMENTUM_SWITCH_ABOVE_HELD_GAP)
    if leader_yes + 1e-12 < held_mark_yes + gap:
        return None, None

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

    rank = yes_rank_by_market_prob(market_id, siblings) if siblings else 1
    peer_ids = sorted({str(x).strip() for x in ev_mids if str(x).strip()})
    top_changes = top_price_change_peers(
        peer_ids, window_sec=C.MOMENTUM_WINDOW_SECONDS, top_n=3
    )

    min_lead_mkt = float(
        getattr(settings, "min_lead_over_runner_up", C.MIN_LEAD_OVER_RUNNER_UP)
    )
    market_lead_gap, runner_up_yes = (
        market_yes_lead_gap_vs_runner_up(market_id, mkt, siblings)
        if siblings
        else (1.0, 0.0)
    )

    mom_signal, mom_rise = momentum_entry_signal(
        market_id,
        rise_threshold=C.MOMENTUM_ENTRY_RISE,
        window_sec=C.MOMENTUM_WINDOW_SECONDS,
    )
    mom_min = float(getattr(settings, "momentum_min_price", C.MOMENTUM_MIN_PRICE))
    mom_max = float(getattr(settings, "momentum_max_entry", C.MOMENTUM_MAX_ENTRY))

    # double momentum: ≥30% rise → wider price band (0.40-0.80)
    dbl_rise_thr = float(getattr(settings, "double_momentum_entry_rise", C.DOUBLE_MOMENTUM_ENTRY_RISE))
    dbl_min = float(getattr(settings, "double_momentum_min_price", C.DOUBLE_MOMENTUM_MIN_PRICE))
    dbl_max = float(getattr(settings, "double_momentum_max_price", C.DOUBLE_MOMENTUM_MAX_PRICE))
    is_double_momentum = (
        mom_rise >= dbl_rise_thr
        and dbl_min - 1e-12 <= mkt <= dbl_max + 1e-12
        and rank == 1
        # User requested: Double momentum doesn't need to lead by min_lead_mkt (X%)
    )

    # standard momentum: ≥15% rise → normal band (0.65-0.80)
    is_momentum_entry = is_double_momentum or (
        mom_signal
        and mom_min - 1e-12 <= mkt <= mom_max + 1e-12
        and rank == 1
        and market_lead_gap + 1e-12 >= min_lead_mkt
    )

    _, _, momentum_15m = price_change_in_window(market_id, C.MOMENTUM_WINDOW_SECONDS)

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
        )
        _log_decision(td)
        _momentum_eval_debug_line(
            {
                "market_id": market_id,
                "city": city,
                "momentum_15m_pct": round(float(momentum_15m) * 100.0, 2),
                "window_rise_pct": round(float(mom_rise) * 100.0, 2),
                "yes_rank": int(rank),
                "market_lead_gap": round(float(market_lead_gap), 4),
                "runner_up_yes": round(float(runner_up_yes), 4),
                "min_lead_required": round(float(min_lead_mkt), 4),
                "top_peers_15m_pct": [
                    {"id": pid, "chg_pct": round(float(ch) * 100.0, 2)}
                    for pid, ch in top_changes
                ],
                "decision": decision,
                "reason": reason[:160],
                "momentum_relaxed_buy": relaxed,
                "entry_type": etype,
                "is_double_momentum": is_double_momentum,
            }
        )
        return td

    # 0. event-level churn — block after repeated losses on same event
    from strategy.churn import churn_event_allows_buy
    if ev_id and not churn_event_allows_buy(state, ev_id):
        return finish("SKIP", f"event_churn_cooldown active for {ev_id}")

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

    # 7. competition filter — 15% lead required (skipped for momentum entries)
    comp = evaluate_competition(
        client,
        market,
        mkt,
        float(settings.min_lead_over_runner_up),
        event_cache,
        consensus_c=consensus_c,
        sigma_c_setting=float(settings.research_sigma_c),
    )
    if settings.enable_competition_filter and not comp.passes and not is_momentum_entry:
        return finish(
            "SKIP",
            f"competition_fail gap={comp.gap:.4f} < min_lead={settings.min_lead_over_runner_up:.4f}",
            competition=comp,
        )

    # 9. negative momentum — don't buy into a falling bucket
    if momentum_15m < -0.10 and not is_momentum_entry:
        return finish(
            "SKIP",
            f"negative_momentum {momentum_15m:+.1%} in 15m",
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
        return float(getattr(settings, "stop_loss_double_momentum", C.STOP_LOSS_DOUBLE_MOMENTUM))
    if et == "momentum":
        return float(getattr(settings, "stop_loss_momentum", C.STOP_LOSS_MOMENTUM))
    return float(getattr(settings, "stop_loss_normal", C.STOP_LOSS_NORMAL))


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

    # 1. fast stop-loss: absolute price drop from peak in 15m window, mark below entry
    fast_exit, dd = should_fast_exit(
        market_id,
        drop_threshold=C.MOMENTUM_FAST_EXIT_DROP,
        window_sec=C.MOMENTUM_WINDOW_SECONDS,
    )
    if fast_exit and mark < entry:
        return "momentum-stop-loss", gamma_prob

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

    # 2. competitor surge: sibling bucket +15% in 15 minutes
    peers = peer_market_ids_excluding_self(client, market, event_cache)
    if peers:
        surged, surge_mid, surge_pct = peer_surge_detected(
            peers,
            surge_threshold=C.MOMENTUM_COMPETITOR_SURGE,
            window_sec=C.MOMENTUM_WINDOW_SECONDS,
        )
        if surged:
            return "competitor-surge", gamma_prob

    # 3. time-decay exit
    decay_exit, decay_reason = should_time_decay_exit(
        trade,
        current_price=mark,
        decay_hours=C.TIME_DECAY_HOURS,
        min_gain_pct=C.TIME_DECAY_MIN_GAIN,
        max_price_for_decay=C.TIME_DECAY_MAX_PRICE,
    )
    if decay_exit:
        return "time-decay", gamma_prob

    return None, None
