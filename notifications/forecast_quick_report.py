"""Telegram HTML for /forecast: positions first, then °C rows (cache JSON first)."""

from __future__ import annotations

import os
from typing import List, Optional

from polymarket_client import PolymarketClient

from config.settings import get_effective_settings
from forecast.cache_store import forecast_cache_age_sec, load_forecast_cache
from forecast.forecast_service import get_forecast_max_for_city_day
from forecast.parse_title import parse_highest_temp_title
from notifications.forecast_cache_fmt import (
    english_temp_summary_lines,
    read_cached_om_cons_for_market,
)
from research.calibration_apply import bias_for_city
from strategy.research_signal import research_edge_decision
from strategy.time_utils import format_report_local_hhmm
from telegram_bot import tg_escape


def _fmt_src(found: bool) -> str:
    if found:
        return "<code>forecast_cache.json</code> (no live refresh on this command)"
    return "<code>live API</code> (cache miss — run <code>python -m forecast</code>)"


def build_open_temp_forecast_quick_html(client: PolymarketClient) -> str:
    """
    /forecast: lists open positions, then per row reads OM/consensus from
    ``data/forecast_cache.json`` when a matching group exists (instant).
    If no row in the file, falls back to ``get_forecast_max_for_city_day`` once.
    """
    settings = get_effective_settings()
    blend = bool(getattr(settings, "enable_openweather_forecast", False))
    ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip() if blend else ""
    positions = client.get_open_positions()
    loc = format_report_local_hhmm()
    age = forecast_cache_age_sec()
    age_s = f"{age:.0f}s ago" if age is not None else "no file"
    has_file = load_forecast_cache() is not None

    parts: List[str] = [
        f"🌡 <b>Forecast</b>  <code>{tg_escape(loc)}</code>",
        f"<i>Primary source: <code>forecast_cache.json</code> "
        f"(written by digest; age ~{tg_escape(age_s)}). "
        f"File present: <code>{'yes' if has_file else 'no'}</code>.</i>",
        "<i>Calibration bias still documented from <code>calibration_latest.json</code>; "
        "adjusted column follows the same numbers as the cache row.</i>",
        "",
        "<b>1) Open positions</b>",
    ]
    if not positions:
        parts.append("<i>(none)</i>")
    else:
        for idx, pos in enumerate(positions, start=1):
            title = str(pos.get("title") or "?")
            t_short = title if len(title) <= 140 else title[:137] + "…"
            mark = float(pos.get("cur_price") or 0)
            mid = str(pos.get("market_id") or "").strip() or "—"
            oc = str(pos.get("outcome") or "").strip()
            oc_s = f" · <code>{tg_escape(oc)}</code>" if oc else ""
            parts.append(
                f"<b>{idx}.</b> {tg_escape(t_short)}\n"
                f"   mark=<code>{mark:.2%}</code>{oc_s} · gamma <code>{tg_escape(mid)}</code>"
            )

    parts.extend(["", "<b>2) Temperature rows (per position)</b>"])

    if not positions:
        parts.append("<i>nothing to show.</i>")
        return "\n".join(parts)

    for idx, pos in enumerate(positions, start=1):
        title = str(pos.get("title") or "")
        mark = float(pos.get("cur_price") or 0)
        mid = str(pos.get("market_id") or "").strip()
        parts.append(f"<u>#{idx}</u>")
        p = parse_highest_temp_title(title)
        if not p:
            parts.append(
                "   <i>not a parsable highest-temperature title — no °C row</i>"
            )
            continue

        om: Optional[float]
        cons: Optional[float]
        found: bool
        om, cons, found = read_cached_om_cons_for_market(mid or None, title)
        if not found:
            om_l, _ow_l, cons_l = get_forecast_max_for_city_day(
                p.city_key,
                p.event_date,
                p.tz_name,
                openweather_api_key=ow_key,
                use_openweather=blend,
            )
            om, cons = om_l, cons_l

        parts.append(f"   <i>source:</i> {_fmt_src(found)}")
        parts.extend(english_temp_summary_lines(p, om, cons))
        bias = bias_for_city(p.city_key)
        parts.append(f"   <b>calibration_bias_c</b>: <code>{bias:+.2f}°C</code>")
        if blend:
            parts.append("   <i>OpenWeather blend enabled in runtime (digest rows may include OW).</i>")

        adj = cons if cons is not None else om
        if mark > 1e-9 and adj is not None:
            rd = research_edge_decision(
                p,
                float(adj),
                float(mark),
                sigma_c=float(settings.research_sigma_c),
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
            gate_lbl = "on" if settings.research_edge_gate_buy else "off"
            parts.append(
                "   <b>model_stats</b> "
                f"(σ=<code>{settings.research_sigma_c:g}</code> °C, gate <code>{gate_lbl}</code>): "
                f"P_model≈<code>{rd.implied_yes:.1%}</code> "
                f"· Δ vs mark <code>{rd.edge * 100:+.1f} pp</code> "
                f"· hurdle≈<code>{rd.required_edge * 100:.1f} pp</code>"
            )
        parts.append("")

    while parts and parts[-1] == "":
        parts.pop()
    return "\n".join(parts)
