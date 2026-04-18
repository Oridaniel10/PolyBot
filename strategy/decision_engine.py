"""Probabilistic buy decision: model vs market edge, risk guards, event context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional, Set, Tuple

from config.settings import RuntimeSettings
from forecast.parse_title import ParsedTempMarket
from polymarket_client import gamma_event_ids_for_market
from research.calibration_apply import resolved_research_sigma_c
from research.probability_from_forecast import implied_yes_prob
from strategy.probability import parse_market_probability
from strategy.research_signal import ResearchEdgeDecision, research_edge_decision

import time


@dataclass(frozen=True)
class PeerContext:
    """Sibling-bucket context for the same Polymarket event."""

    max_peer_yes_rise_ratio: float = 0.0
    leader_market_yes: float = 0.0


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
    research: Optional[ResearchEdgeDecision] = None


def collect_event_id_and_market_ids(
    market: Dict[str, Any],
    event_cache: Dict[str, Any],
    client: Any,
) -> Tuple[Optional[str], Set[str]]:
    """
    returns:
    - (first gamma event id, set of market id strings in that event) or (None, empty).
    """
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


def event_open_position_count(active_trades: Dict[str, Any], event_market_ids: Set[str]) -> int:
    if not event_market_ids:
        return 0
    n = 0
    for mid in active_trades.keys():
        if str(mid).strip() in event_market_ids:
            n += 1
    return n


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


def set_event_buy_cooldown(state: Dict[str, Any], event_id: str, cooldown_sec: float) -> None:
    if not event_id or cooldown_sec <= 0:
        return
    raw = state.setdefault("event_buy_cooldown", {})
    if not isinstance(raw, dict):
        raw = {}
        state["event_buy_cooldown"] = raw
    raw[str(event_id).strip()] = time.time() + float(cooldown_sec)


def leader_market_yes_from_event_markets(markets: Any) -> float:
    """max Gamma YES among event markets (rough crowd leader)."""
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


def distribution_is_flat(model_prob: float, settings: RuntimeSettings) -> bool:
    """
    true if this bucket's model mass is too low to treat as a confident view
    (flat tail of the belief distribution for this strike).
    """
    peak_min = float(getattr(settings, "decision_min_model_peak_prob", 0.12))
    return model_prob + 1e-9 < peak_min


def evaluate_opportunity(
    parsed: ParsedTempMarket,
    consensus_c: float,
    market_yes: float,
    settings: RuntimeSettings,
    state: Dict[str, Any],
    *,
    event_id: Optional[str],
    event_market_ids: Set[str],
    peer_context: Optional[PeerContext] = None,
) -> TradeDecision:
    """
    conservative BUY / SKIP from calibrated forecast vs CLOB YES.

    params:
    - parsed: parsed temperature market.
    - consensus_c: bias-adjusted consensus °C.
    - market_yes: CLOB YES price (0–1).
    - settings: effective runtime settings.
    - state: bot state (cooldowns, positions).
    - event_id: gamma event id if known.
    - event_market_ids: all market ids in the event.
    - peer_context: optional peer momentum / leader YES.

    returns:
    - TradeDecision with decision BUY or SKIP and reason string.
    """
    city = parsed.city_key
    date_iso = parsed.event_date.isoformat() if isinstance(parsed.event_date, date) else ""
    bucket = (parsed.raw_title or "")[:160]
    sigma = resolved_research_sigma_c(float(settings.research_sigma_c), city)
    model_p = implied_yes_prob(parsed, float(consensus_c), sigma_c=sigma)
    mkt = float(market_yes)
    edge = float(model_p) - mkt

    def finish(
        decision: str,
        reason: str,
        research: Optional[ResearchEdgeDecision] = None,
    ) -> TradeDecision:
        return TradeDecision(
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
            research=research,
        )

    if event_id and event_buy_cooldown_active(state, event_id):
        return finish(
            "SKIP",
            f"event_buy_cooldown active for event_id={event_id}",
        )

    max_mkt = float(getattr(settings, "max_market_prob_for_buy", 0.75))
    if mkt > max_mkt + 1e-9:
        return finish("SKIP", f"market_prob {mkt:.4f} > max_market_prob_for_buy={max_mkt:.4f}")

    min_model = float(getattr(settings, "min_model_prob_for_buy", 0.10))
    if model_p + 1e-9 < min_model:
        return finish(
            "SKIP",
            f"model_prob {model_p:.4f} < min_model_prob_for_buy={min_model:.4f}",
        )

    if distribution_is_flat(model_p, settings):
        return finish(
            "SKIP",
            f"model_distribution_flat model_prob={model_p:.4f} "
            f"(below decision_min_model_peak_prob)",
        )

    if peer_context and getattr(settings, "peer_surge_skip_buy_enabled", False):
        thr = float(getattr(settings, "peer_surge_skip_buy_rise_threshold", 0.25))
        if peer_context.max_peer_yes_rise_ratio + 1e-9 >= thr:
            return finish(
                "SKIP",
                f"peer_surge_skip peer_rise={peer_context.max_peer_yes_rise_ratio:.3f} "
                f">= {thr:.3f}",
            )

    max_pos = int(getattr(settings, "max_positions_per_event", 1))
    active = state.get("active_trades") or {}
    if not isinstance(active, dict):
        active = {}
    open_n = event_open_position_count(active, event_market_ids)
    if max_pos > 0 and open_n >= max_pos:
        return finish(
            "SKIP",
            f"max_positions_per_event open_in_event={open_n} max={max_pos}",
        )

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
    if not rd.passes_gate:
        return finish("SKIP", rd.skip_reason or "research_edge_fail", research=rd)

    return finish("BUY", "passed_decision_engine", research=rd)
