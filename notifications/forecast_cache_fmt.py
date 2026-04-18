"""Per-position forecast snippet for Telegram (under each open position).

Numbers always come from ``get_forecast_max_for_city_day`` (live Open-Meteo,
then ``adjust_consensus_optional`` / calibration_latest). OpenWeather is blended
in only when ``enable_openweather_forecast`` is true in runtime settings.

Portfolio snippets merge ``forecast_cache.json`` with optional live fill when
° rows are empty. The Telegram ``/forecast`` command reads the JSON first (see
``forecast_quick_report``).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from telegram_bot import tg_escape

from config.settings import get_effective_settings
from forecast.cache_store import load_forecast_cache
from forecast.forecast_service import get_forecast_max_for_city_day
from forecast.parse_title import (
    BracketKind,
    ParsedTempMarket,
    forecast_contradicts_strongly,
    forecast_supports_yes,
    parse_highest_temp_title,
)
from strategy.city_tz import city_local_datetime_now_str
from strategy.research_signal import research_edge_decision


def _enrich_group_with_live_model(g: Dict[str, Any], held_title: str) -> Dict[str, Any]:
    """If digest cached null °C (stale run, API blip), fill from Open-Meteo (+ optional OW)."""
    if g.get("consensus_c") is not None or g.get("open_meteo_c") is not None:
        return g
    p = parse_highest_temp_title(held_title)
    if not p:
        return g
    settings = get_effective_settings()
    blend = bool(getattr(settings, "enable_openweather_forecast", False))
    ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip() if blend else ""
    om, _ow, cons = get_forecast_max_for_city_day(
        p.city_key,
        p.event_date,
        p.tz_name,
        openweather_api_key=ow_key,
        use_openweather=blend,
    )
    if om is None and cons is None:
        return g
    out = dict(g)
    out["open_meteo_c"] = om
    out["consensus_c"] = cons if cons is not None else om
    return out


def _synthetic_group_from_title(held_title: str) -> Optional[Dict[str, Any]]:
    """When market id is missing from digest cache, still show model vs bracket from title."""
    p = parse_highest_temp_title(held_title)
    if not p:
        return None
    settings = get_effective_settings()
    blend = bool(getattr(settings, "enable_openweather_forecast", False))
    ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip() if blend else ""
    om, _ow, cons = get_forecast_max_for_city_day(
        p.city_key,
        p.event_date,
        p.tz_name,
        openweather_api_key=ow_key,
        use_openweather=blend,
    )
    return {
        "city_key": p.city_key,
        "date_iso": p.event_date.isoformat(),
        "open_meteo_c": om,
        "consensus_c": cons,
        "markets": [],
    }


def _bracket_label(p: ParsedTempMarket) -> str:
    if p.bracket == BracketKind.AT_MOST:
        return f"≤{p.threshold_c:g}°C"
    if p.bracket == BracketKind.AT_LEAST:
        return f"≥{p.threshold_c:g}°C"
    return f"≈{p.threshold_c:g}°C (exact bucket)"


def _city_display_name(city_key: str) -> str:
    return (city_key or "").replace("_", " ").strip().title()


def _float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def english_temp_summary_lines(p: ParsedTempMarket, om: Any, cons: Any) -> List[str]:
    """Three English lines: Polymarket bracket, OM raw, calibrated value (per city)."""
    city = _city_display_name(p.city_key)
    bracket = _bracket_label(p)

    def _fmt(v: Any) -> str:
        if v is None:
            return "NULL"
        try:
            return f"{float(v):.1f}°C"
        except (TypeError, ValueError):
            return "NULL"

    om_s = _fmt(om)
    adj = cons if cons is not None else om
    adj_s = _fmt(adj)
    return [
        f"      <b>max_temperature_polymarket_bracket in {tg_escape(city)}:</b> "
        f"<code>{tg_escape(bracket)}</code>",
        f"      <b>max_temp_open_meteo_live in {tg_escape(city)}:</b> "
        f"<code>{tg_escape(om_s)}</code>",
        f"      <b>max_temp_with_calibration in {tg_escape(city)}:</b> "
        f"<code>{tg_escape(adj_s)}</code>",
    ]


def _temp_om_cal_one_line(p: ParsedTempMarket, om: Any, cons: Any) -> str:
    """single compact line for Telegram portfolio (OM + calibrated °C)."""

    def _fmt(v: Any) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v):.1f}°C"
        except (TypeError, ValueError):
            return "—"

    om_s = _fmt(om)
    adj = cons if cons is not None else om
    adj_s = _fmt(adj)
    return f"      OM <code>{tg_escape(om_s)}</code> · cal <code>{tg_escape(adj_s)}</code>"


def _short_market_id(mid: str) -> str:
    s = str(mid or "").strip()
    if len(s) <= 10:
        return s
    return f"…{s[-8:]}"


def read_cached_om_cons_for_market(
    market_id: Optional[str], title: str
) -> Tuple[Optional[float], Optional[float], bool]:
    """
    Reads open_meteo_c and consensus_c from forecast_cache.json only (no HTTP).

    returns:
    - (om_c, consensus_c, found_group)
    """
    mid = str(market_id or "").strip()
    title = str(title or "").strip()
    cache = load_forecast_cache()
    if not cache:
        return None, None, False
    groups = cache.get("groups") or []
    if not isinstance(groups, list):
        return None, None, False

    def from_g(g: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], bool]:
        return (
            _float_or_none(g.get("open_meteo_c")),
            _float_or_none(g.get("consensus_c")),
            True,
        )

    if mid:
        for g in groups:
            if not isinstance(g, dict):
                continue
            members = g.get("member_market_ids")
            if isinstance(members, list) and mid in {str(x).strip() for x in members if x}:
                return from_g(g)
            for m in g.get("markets") or []:
                if isinstance(m, dict) and str(m.get("id") or "").strip() == mid:
                    return from_g(g)

    p = parse_highest_temp_title(title)
    if p:
        want_ck = p.city_key.strip().lower()
        want_day = p.event_date.isoformat()
        for g in groups:
            if not isinstance(g, dict):
                continue
            g_ck = str(g.get("city_key") or "").strip().lower()
            g_day = str(g.get("date_iso") or "").strip()
            if g_ck == want_ck and g_day == want_day:
                return from_g(g)

    return None, None, False


def _held_vs_model_lines(
    g: Dict[str, Any],
    held_mid: str,
    held_title: str,
    mark_yes: Optional[float],
) -> List[str]:
    cons = g.get("consensus_c")
    om = g.get("open_meteo_c")
    model_max = cons if cons is not None else om
    model_max_f = float(model_max) if model_max is not None else None

    lines: List[str] = []
    p = parse_highest_temp_title(held_title)
    mid_short = _short_market_id(held_mid)
    if p:
        bkt = tg_escape(_bracket_label(p))
        yes_part = (
            f" · YES <code>{mark_yes:.1%}</code>"
            if mark_yes is not None and float(mark_yes) > 1e-9
            else ""
        )
        lines.append(
            f"      <b>Bracket</b> <code>{bkt}</code>{yes_part} · id <code>{tg_escape(mid_short)}</code>"
        )
    else:
        lines.append(
            f"      <b>Market</b> <code>{tg_escape(mid_short)}</code> "
            "<i>(not parsed as temp bracket)</i>"
        )

    if p:
        lines.append(_temp_om_cal_one_line(p, om, cons))

    if model_max_f is not None:
        st_fc = get_effective_settings()
        margin = float(st_fc.forecast_contradict_margin_c)
        exact_slack = float(st_fc.forecast_exact_bucket_support_slack_c)
        if p:
            if forecast_contradicts_strongly(model_max_f, p, margin):
                lines.append("      ⚠️ <u>model vs YES</u> · <b>contradicts</b>")
            elif forecast_supports_yes(
                model_max_f, p, exact_slack_c=exact_slack,
            ):
                lines.append("      ✓ <u>model vs YES</u> · <b>consistent</b>")
            else:
                lines.append("      ○ <u>model vs YES</u> · neutral")
        if (
            p
            and mark_yes is not None
            and float(mark_yes) > 1e-9
        ):
            rd = research_edge_decision(
                p,
                float(model_max_f),
                float(mark_yes),
                sigma_c=float(st_fc.research_sigma_c),
                min_edge=float(st_fc.research_min_edge),
                min_edge_after_fees_add=float(st_fc.research_min_edge_after_fees_add),
                fee_rate_for_drag=float(st_fc.research_weather_taker_fee_rate),
                gate_enabled=bool(st_fc.research_edge_gate_buy),
                soft_match_enabled=bool(st_fc.research_crowd_soft_match),
                soft_band=float(st_fc.research_crowd_soft_band),
                soft_edge_factor=float(st_fc.research_crowd_soft_edge_factor),
                disagree_extra_edge=float(st_fc.research_crowd_disagree_extra_edge),
                disagree_gap=float(st_fc.research_crowd_disagree_gap),
                implied_soft_floor=float(st_fc.research_edge_implied_soft_floor),
                implied_soft_boost_mult=float(st_fc.research_edge_implied_soft_boost),
            )
            gate_lbl = "on" if st_fc.research_edge_gate_buy else "off"
            soft_note = ""
            if rd.edge_soft_boost > 1e-9:
                soft_note = (
                    f" · rawΔ<code>{rd.edge_raw * 100:+.1f}pp</code>"
                    f" · soft+<code>{rd.edge_soft_boost * 100:.1f}pp</code>"
                )
            lines.append(
                "      <b>Research</b> "
                f"gate <code>{gate_lbl}</code> σ<code>{st_fc.research_sigma_c:g}</code> · "
                f"P≈<code>{rd.implied_yes:.1%}</code> "
                f"· Δ<code>{rd.edge * 100:+.1f}pp</code>{soft_note} "
                f"· bar≥<code>{rd.required_edge * 100:.1f}pp</code> · "
                f"fee≈<code>{rd.fee_drag * 100:.1f}pp</code>"
            )
    else:
        if not p:
            lines.append("      <i>no °C model row</i>")
    return lines


def _fc_block_for_group(
    g: Dict[str, Any],
    held_mid: str = "",
    held_title: str = "",
    mark_yes: Optional[float] = None,
) -> str:
    city = str(g.get("city_key") or "")
    day = str(g.get("date_iso") or "")
    city_disp = tg_escape((city or "").replace("_", " ").strip().title())
    local_now = city_local_datetime_now_str(held_title)
    local_bit = (
        f" · <i>Local now</i> <code>{tg_escape(local_now)}</code>"
        if local_now
        else ""
    )
    lines: List[str] = [
        f"   <u>🌡 <b>{city_disp}</b> · <code>{tg_escape(day)}</code>{local_bit}</u>",
    ]
    if held_mid:
        lines.extend(_held_vs_model_lines(g, held_mid, held_title, mark_yes))
    else:
        cons = g.get("consensus_c")
        om = g.get("open_meteo_c")
        val = cons if cons is not None else om
        if val is not None:
            lines.append(f"      Open-Meteo (calibrated) <b>~{float(val):.1f}°C</b>")
        else:
            lines.append("      <i>no °C from model (geocode or API)</i>")
    return "\n".join(lines)


def portfolio_position_forecast_html(
    market_id: Optional[str],
    position_title: Optional[str] = None,
    mark_yes: Optional[float] = None,
) -> str:
    mid = str(market_id or "").strip()
    title = str(position_title or "").strip()
    # show snippet when we can match digest by id, or title-only (e.g. missing gamma id)
    mid_disp = mid if mid else "—"
    if not mid and not parse_highest_temp_title(title):
        return ""

    cache = load_forecast_cache()
    if mid and cache:
        for g in cache.get("groups") or []:
            if not isinstance(g, dict):
                continue
            members = g.get("member_market_ids")
            if isinstance(members, list) and mid in {str(x).strip() for x in members if x}:
                g2 = _enrich_group_with_live_model(g, title)
                return _fc_block_for_group(g2, mid_disp, title, mark_yes)
            for m in g.get("markets") or []:
                if not isinstance(m, dict):
                    continue
                if str(m.get("id") or "").strip() == mid:
                    g2 = _enrich_group_with_live_model(g, title)
                    return _fc_block_for_group(g2, mid_disp, title, mark_yes)
    syn = _synthetic_group_from_title(title)
    if syn is not None:
        return _fc_block_for_group(syn, mid_disp, title, mark_yes)
    return ""
