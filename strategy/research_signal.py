"""Model-implied P(YES) vs market for optional buy gate (uses calibration-adjusted °C)."""

from __future__ import annotations

from dataclasses import dataclass

from forecast.parse_title import ParsedTempMarket
from research.calibration_apply import resolved_research_sigma_c
from research.probability_from_forecast import implied_yes_prob


def taker_fee_fraction_per_leg(p_yes: float, fee_rate: float) -> float:
    """
    fee ≈ feeRate × p × (1−p) per Polymarket taker leg; returns fee / notional.
    """
    p = max(1e-6, min(1.0 - 1e-6, float(p_yes)))
    r = max(0.0, float(fee_rate))
    return r * p * (1.0 - p)


def round_trip_fee_prob_drag(p_yes: float, fee_rate: float) -> float:
    """Rough sum of buy + sell taker fee fractions (same p)."""
    return 2.0 * taker_fee_fraction_per_leg(p_yes, fee_rate)


def model_implied_yes(
    parsed: ParsedTempMarket,
    consensus_c: float,
    sigma_c: float = 2.0,
) -> float:
    return implied_yes_prob(parsed, float(consensus_c), sigma_c=float(sigma_c))


def edge_implied_vs_market(implied_yes: float, market_yes_p: float) -> float:
    """positive => model P(YES) above market (YES looks underpriced)."""
    return float(implied_yes) - float(market_yes_p)


@dataclass(frozen=True)
class ResearchEdgeDecision:
    implied_yes: float
    edge: float
    edge_raw: float
    edge_soft_boost: float
    consensus_c: float
    required_edge: float
    fee_drag: float
    passes_gate: bool
    skip_reason: str


def research_edge_decision(
    parsed: ParsedTempMarket,
    consensus_c: float,
    clob_yes: float,
    *,
    sigma_c: float,
    min_edge: float,
    min_edge_after_fees_add: float,
    fee_rate_for_drag: float,
    gate_enabled: bool,
    soft_match_enabled: bool,
    soft_band: float,
    soft_edge_factor: float,
    disagree_extra_edge: float,
    disagree_gap: float,
    implied_soft_floor: float = 0.30,
    implied_soft_boost_mult: float = 1.0,
) -> ResearchEdgeDecision:
    eff_sigma = resolved_research_sigma_c(float(sigma_c), parsed.city_key)
    implied = model_implied_yes(parsed, consensus_c, sigma_c=eff_sigma)
    edge_raw = edge_implied_vs_market(implied, clob_yes)
    boost = 0.0
    fl = float(implied_soft_floor)
    bm = float(implied_soft_boost_mult)
    if fl > 1e-12 and bm > 1e-12 and implied > fl:
        boost = bm * (float(implied) - fl)
    edge = edge_raw + boost
    fee_drag = round_trip_fee_prob_drag(clob_yes, fee_rate_for_drag)
    required = (
        max(0.0, float(min_edge)) + max(0.0, float(min_edge_after_fees_add)) + fee_drag
    )

    if soft_match_enabled and abs(clob_yes - implied) <= max(0.0, float(soft_band)):
        required *= max(0.1, min(1.0, float(soft_edge_factor)))

    eg = float(disagree_gap)
    if float(disagree_extra_edge) > 0 and eg > 0 and clob_yes > implied + eg:
        required += float(disagree_extra_edge)

    if not gate_enabled:
        return ResearchEdgeDecision(
            implied_yes=implied,
            edge=edge,
            edge_raw=edge_raw,
            edge_soft_boost=boost,
            consensus_c=float(consensus_c),
            required_edge=required,
            fee_drag=fee_drag,
            passes_gate=True,
            skip_reason="",
        )

    passes = edge + 1e-9 >= required
    skip = ""
    if not passes:
        extra = ""
        if boost > 1e-12:
            extra = f" raw={edge_raw:.4f} soft_boost=+{boost:.4f}"
        skip = (
            f"research_edge edge={edge:.4f} < required={required:.4f} "
            f"(implied={implied:.4f} clob={clob_yes:.4f}){extra}"
        )
    return ResearchEdgeDecision(
        implied_yes=implied,
        edge=edge,
        edge_raw=edge_raw,
        edge_soft_boost=boost,
        consensus_c=float(consensus_c),
        required_edge=required,
        fee_drag=fee_drag,
        passes_gate=passes,
        skip_reason=skip,
    )


def edge_size_multiplier(
    edge: float,
    required_edge: float,
    *,
    scale_enabled: bool,
    slope: float,
    cap_mult: float,
) -> float:
    """extra multiplier on top of forecast_usd_factor when edge clears bar."""
    if not scale_enabled or slope <= 0:
        return 1.0
    excess = max(0.0, float(edge) - float(required_edge))
    m = 1.0 + float(slope) * excess
    cap = max(1.0, float(cap_mult))
    return max(0.1, min(cap, m))
