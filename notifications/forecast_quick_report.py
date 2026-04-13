"""Short Telegram HTML: Open-Meteo daily max for open temp positions (on-demand)."""

from __future__ import annotations

import os
from datetime import timedelta
from typing import List

from polymarket_client import PolymarketClient

from config.settings import get_effective_settings
from forecast.forecast_service import get_forecast_max_for_city_day
from forecast.parse_title import parse_highest_temp_title
from strategy.time_utils import format_report_local_hhmm, now_in_report_timezone
from telegram_bot import tg_escape


def build_open_temp_forecast_quick_html(client: PolymarketClient) -> str:
    """
    One line per open position with a parsable highest-temperature title.
    Event date must be within yesterday..tomorrow (report TZ) so /forecast stays short.
    """
    settings = get_effective_settings()
    ow_key = ""
    if getattr(settings, "enable_openweather_forecast", False):
        ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
    today = now_in_report_timezone().date()
    lo = today - timedelta(days=1)
    hi = today + timedelta(days=1)
    positions = client.get_open_positions()
    header = (
        f"🌡 <b>Forecast</b>  <code>{tg_escape(format_report_local_hhmm())}</code>\n"
        f"<i>Open-Meteo daily max °C (consensus if OW on). "
        f"Events {lo} … {hi} (report calendar).</i>\n"
    )
    body_lines: List[str] = []
    for pos in positions:
        title = str(pos.get("title") or "")
        p = parse_highest_temp_title(title)
        if not p:
            continue
        if p.event_date < lo or p.event_date > hi:
            continue
        om, _ow, cons = get_forecast_max_for_city_day(
            p.city_key,
            p.event_date,
            p.tz_name,
            openweather_api_key=ow_key,
        )
        val = cons if cons is not None else om
        city_disp = p.city_key.title()
        day_s = p.event_date.isoformat()
        if val is not None:
            body_lines.append(
                f"• <b>{tg_escape(city_disp)}</b> <code>{tg_escape(day_s)}</code>: "
                f"<code>~{val:.1f}°C</code>"
            )
        else:
            body_lines.append(
                f"• <b>{tg_escape(city_disp)}</b> <code>{tg_escape(day_s)}</code>: "
                "<i>no model</i>"
            )
    if not body_lines:
        return (
            header
            + "\n<i>No open temp markets in the date window, or titles not parsed.</i>"
        )
    return header + "\n" + "\n".join(body_lines)
