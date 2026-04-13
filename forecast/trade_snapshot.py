"""Telegram lines: model max °C for a temp market (no sibling YES ranks)."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from polymarket_client import PolymarketClient
from telegram_bot import tg_escape

from forecast.forecast_service import get_forecast_max_for_city_day
from forecast.parse_title import parse_highest_temp_title


def temp_market_trade_context_html(
    client: PolymarketClient,
    market: Dict[str, Any],
) -> str:
    """
    Open-Meteo (+ optional OW) daily max for this market's city-day.
    """
    title = str(market.get("question") or market.get("title") or "")
    anchor = parse_highest_temp_title(title)
    if not anchor:
        return ""

    from config.settings import get_effective_settings

    settings = get_effective_settings()
    ow_key = ""
    if getattr(settings, "enable_openweather_forecast", False):
        ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip()

    om, ow, cons = get_forecast_max_for_city_day(
        anchor.city_key,
        anchor.event_date,
        anchor.tz_name,
        openweather_api_key=ow_key,
    )
    fc = []
    if cons is not None:
        fc.append(f"consensus ~{cons:.1f}°C")
    elif om is not None:
        fc.append(f"OM ~{om:.1f}°C")
    if ow is not None:
        fc.append(f"OW ~{ow:.1f}°C")
    fc_s = " · ".join(fc) if fc else "no fetch"

    lines: List[str] = [
        f"🌡 <i>{tg_escape(fc_s)}</i> · "
        f"<code>{tg_escape(anchor.city_key.title())}</code> "
        f"<code>{tg_escape(anchor.event_date.isoformat())}</code>",
    ]
    return "\n".join(lines)
