"""Telegram HTML snippets for research-based buy/skip context."""

from __future__ import annotations

import time
from typing import Dict, Optional

from telegram_bot import tg_escape

from strategy.decision_core import TradeDecision


def format_research_context_html(
    *,
    consensus_c: Optional[float],
    implied_yes: Optional[float],
    edge: Optional[float],
    required_edge: Optional[float],
    fee_drag: Optional[float],
    clob_yes: Optional[float],
    edge_raw: Optional[float] = None,
    edge_soft_boost: Optional[float] = None,
) -> str:
    if consensus_c is None and implied_yes is None:
        return ""
    parts = ["📊 <b>Research</b>"]
    if consensus_c is not None:
        parts.append(f"consensus≈<code>{consensus_c:.2f}</code>°C")
    if implied_yes is not None:
        parts.append(f"P_implied≈<code>{implied_yes:.4f}</code>")
    if clob_yes is not None:
        parts.append(f"CLOB <code>{clob_yes:.4f}</code>")
    if edge is not None:
        parts.append(f"edge <code>{edge:+.4f}</code>")
    if (
        edge_soft_boost is not None
        and float(edge_soft_boost) > 1e-9
        and edge_raw is not None
    ):
        parts.append(f"raw <code>{float(edge_raw):+.4f}</code>")
        parts.append(f"soft+ <code>{float(edge_soft_boost):+.4f}</code>")
    if required_edge is not None:
        parts.append(f"req≥<code>{required_edge:.4f}</code>")
    if fee_drag is not None and fee_drag > 1e-6:
        parts.append(f"fee_drag≈<code>{fee_drag:.4f}</code>")
    return " · ".join(parts) + "\n"


_LAST_RESEARCH_SKIP_TG: Dict[str, float] = {}
_LAST_DECISION_SKIP_TG: Dict[str, float] = {}


def decision_skip_telegram_category(reason: str) -> str:
    """
    stable bucket for dedupe; full reason strings include floats that change every tick.
    """
    r = (reason or "").strip()
    if r.startswith("research_edge"):
        return "research_edge"
    if r.startswith("model_prob"):
        return "model_prob_min"
    if r.startswith("market_prob"):
        return "market_prob_max"
    if r.startswith("model_distribution_flat"):
        return "distribution_flat"
    if r.startswith("event_buy_cooldown"):
        return "event_cooldown"
    if r.startswith("max_positions_per_event"):
        return "max_positions"
    if r.startswith("peer_surge_skip"):
        return "peer_surge_skip"
    if r.startswith("leader_yield_blocked"):
        return "leader_yield_blocked"
    return "other"


def decision_skip_telegram_allowed(
    market_id: str, reason: str, cooldown_sec: float
) -> bool:
    """
    returns true if we should send (and records send time).
    dedupes same market_id + skip category within cooldown_sec (in-process only).
    """
    if cooldown_sec <= 0:
        return True
    cat = decision_skip_telegram_category(reason)
    key = f"{market_id}|{cat}"
    now = time.monotonic()
    prev = _LAST_DECISION_SKIP_TG.get(key)
    if prev is not None and (now - prev) < cooldown_sec:
        return False
    _LAST_DECISION_SKIP_TG[key] = now
    if len(_LAST_DECISION_SKIP_TG) > 500:
        cutoff = now - cooldown_sec * 2
        for k in list(_LAST_DECISION_SKIP_TG.keys()):
            if _LAST_DECISION_SKIP_TG[k] < cutoff:
                del _LAST_DECISION_SKIP_TG[k]
    return True


def research_skip_telegram_allowed(
    market_id: str,
    reason: str,
    cooldown_sec: float,
) -> bool:
    """
    returns true if we should send (and records send time).
    dedupes same market_id+reason within cooldown_sec (in-process only).
    """
    if cooldown_sec <= 0:
        return True
    key = f"{market_id}|{reason[:120]}"
    now = time.monotonic()
    prev = _LAST_RESEARCH_SKIP_TG.get(key)
    if prev is not None and (now - prev) < cooldown_sec:
        return False
    _LAST_RESEARCH_SKIP_TG[key] = now
    if len(_LAST_RESEARCH_SKIP_TG) > 500:
        cutoff = now - cooldown_sec * 2
        for k in list(_LAST_RESEARCH_SKIP_TG.keys()):
            if _LAST_RESEARCH_SKIP_TG[k] < cutoff:
                del _LAST_RESEARCH_SKIP_TG[k]
    return True


def format_decision_engine_html(td: TradeDecision) -> str:
    """HTML summary of decision engine BUY context."""
    parts = [
        "🧠 <b>Decision</b>",
        f"<code>{tg_escape(td.decision)}</code>",
        f"σ=<code>{td.sigma_used:.3f}</code>°C",
        f"P_model≈<code>{td.model_prob:.4f}</code>",
        f"P_mkt=<code>{td.market_prob:.4f}</code>",
        f"edge=<code>{td.edge:+.4f}</code>",
        f"<code>{tg_escape(td.reason[:200])}</code>",
    ]
    return " · ".join(parts) + "\n"


def format_decision_skip_html(title: str, skip_reason: str, detail_html: str) -> str:
    return (
        f"🧠 <b>BUY skipped (decision)</b>\n"
        f"{tg_escape(title)}\n"
        f"<code>{tg_escape(skip_reason)}</code>\n"
        f"{detail_html}"
    )


def format_research_skip_html(title: str, skip_reason: str, detail_html: str) -> str:
    return (
        f"🧪 <b>BUY skipped (research)</b>\n"
        f"{tg_escape(title)}\n"
        f"<code>{tg_escape(skip_reason)}</code>\n"
        f"{detail_html}"
    )
