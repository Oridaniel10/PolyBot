"""Multi-line forecast snippet from data/forecast_cache.json (under each position row)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

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


def _enrich_group_with_live_model(g: Dict[str, Any], held_title: str) -> Dict[str, Any]:
    """If digest cached null °C (stale run, API blip), fill from Open-Meteo (+ optional OW)."""
    if g.get("consensus_c") is not None or g.get("open_meteo_c") is not None:
        return g
    p = parse_highest_temp_title(held_title)
    if not p:
        return g
    settings = get_effective_settings()
    ow_key = ""
    if getattr(settings, "enable_openweather_forecast", False):
        ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    om, _ow, cons = get_forecast_max_for_city_day(
        p.city_key,
        p.event_date,
        p.tz_name,
        openweather_api_key=ow_key,
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
    ow_key = ""
    if getattr(settings, "enable_openweather_forecast", False):
        ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    om, _ow, cons = get_forecast_max_for_city_day(
        p.city_key,
        p.event_date,
        p.tz_name,
        openweather_api_key=ow_key,
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
    if p:
        lines.append(
            f"      <b>Your bracket</b> (id <code>{tg_escape(held_mid)}</code>): "
            f"<code>{tg_escape(_bracket_label(p))}</code>"
        )
    else:
        lines.append(
            f"      <b>Your market</b> id <code>{tg_escape(held_mid)}</code> "
            "<i>(title not parsed as temp bracket)</i>"
        )
    if mark_yes is not None and mark_yes > 1e-9:
        lines.append(f"      <b>Your YES (mark)</b>: <code>{mark_yes:.1%}</code>")

    if model_max_f is not None:
        lines.append(
            f"      <b>Model daily max</b> (Open-Meteo/consensus): "
            f"<code>~{model_max_f:.1f}°C</code>"
        )
        if p:
            margin = float(get_effective_settings().forecast_contradict_margin_c)
            if forecast_contradicts_strongly(model_max_f, p, margin):
                lines.append(
                    "      ⚠️ <b>Model vs your YES:</b> <i>strong contradiction</i>"
                )
            elif forecast_supports_yes(model_max_f, p):
                lines.append(
                    "      ✓ <b>Model vs your YES:</b> <i>broadly consistent</i>"
                )
            else:
                lines.append("      ○ <b>Model vs your YES:</b> <i>neutral / weak</i>")
    else:
        lines.append(
            "      <b>Model daily max</b>: <i>unavailable</i> (geocode or API)"
        )
    return lines


def _fc_block_for_group(
    g: Dict[str, Any],
    held_mid: str = "",
    held_title: str = "",
    mark_yes: Optional[float] = None,
) -> str:
    city = str(g.get("city_key") or "")
    day = str(g.get("date_iso") or "")
    lines: List[str] = [
        f"   🌡 <b>{tg_escape(city.title())}</b> · <code>{tg_escape(day)}</code>",
    ]
    if held_mid:
        lines.extend(_held_vs_model_lines(g, held_mid, held_title, mark_yes))
    else:
        cons = g.get("consensus_c")
        om = g.get("open_meteo_c")
        val = cons if cons is not None else om
        if val is not None:
            lines.append(f"      consensus/Open-Meteo <b>~{float(val):.1f}°C</b>")
        else:
            lines.append("      <i>no °C from model (geocode or API)</i>")
    return "\n".join(lines)


def portfolio_position_forecast_html(
    market_id: Optional[str],
    position_title: Optional[str] = None,
    mark_yes: Optional[float] = None,
) -> str:
    mid = str(market_id or "").strip()
    if not mid:
        return ""
    title = str(position_title or "").strip()
    cache = load_forecast_cache()
    if not cache:
        syn = _synthetic_group_from_title(title)
        if syn is not None:
            return _fc_block_for_group(syn, mid, title, mark_yes)
        return ""
    for g in cache.get("groups") or []:
        if not isinstance(g, dict):
            continue
        members = g.get("member_market_ids")
        if isinstance(members, list) and mid in {str(x).strip() for x in members if x}:
            g2 = _enrich_group_with_live_model(g, title)
            return _fc_block_for_group(g2, mid, title, mark_yes)
        for m in g.get("markets") or []:
            if not isinstance(m, dict):
                continue
            if str(m.get("id") or "").strip() == mid:
                g2 = _enrich_group_with_live_model(g, title)
                return _fc_block_for_group(g2, mid, title, mark_yes)
    syn = _synthetic_group_from_title(title)
    if syn is not None:
        return _fc_block_for_group(syn, mid, title, mark_yes)
    return ""
