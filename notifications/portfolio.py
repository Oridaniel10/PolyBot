from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from polymarket_client import PolymarketClient

from config.constants import REPORT_HOURS, TIMEZONE
from config.settings import get_effective_settings
from notifications.terminal import print_terminal_block
from strategy.blacklist_display import collect_blacklist_status_rows
from strategy.city_tz import city_local_time_str
from strategy.time_utils import (
    build_target_day_label,
    format_report_local_hhmm,
    now_in_report_timezone,
)
from telegram_bot import TelegramBot, tg_escape


def format_positions_detail(positions: List[Dict[str, Any]]) -> str:
    if not positions:
        return "(no live open positions — settled or flat rows are hidden)"
    loc = format_report_local_hhmm()
    lines: List[str] = []
    for index, pos in enumerate(positions, start=1):
        title = pos.get("title") or pos.get("market_id", "?")
        lines.append(f"{index}. {title}")
        entry = float(pos.get("avg_price") or 0)
        mark = float(pos.get("cur_price") or 0)
        size = float(pos.get("size") or 0)
        pnl = float(pos.get("cash_pnl") or 0)
        outcome = str(pos.get("outcome") or "")
        oc = f" [{outcome}]" if outcome else ""
        unmapped = (
            "  [no gamma market id — strategy sync may skip]"
            if not pos.get("market_id")
            else ""
        )
        lines.append(
            f"   shares={size:.4f}{oc}  bought@={entry:.4f}  mark@={mark:.4f}  "
            f"pnl=${pnl:.2f}  local={loc}{unmapped}"
        )
    return "\n".join(lines)


def build_full_portfolio_plain(client: PolymarketClient, headline: str) -> str:
    balance = client.get_portfolio_balance(force_allowance_refresh=False)
    positions = client.get_open_positions()
    cash = float(balance.get("cash") or 0)
    pos_val = float(balance.get("positions_market_value") or 0)
    total = float(balance.get("total_value") or (cash + pos_val))
    lines = [
        headline.strip(),
        f"local_time={format_report_local_hhmm()} ({TIMEZONE})",
        "",
        "--- portfolio ---",
        f"cash (free collateral): ${cash:.2f}",
        f"positions (mark-to-market): ${pos_val:.2f}",
        f"portfolio_total (cash + positions): ${total:.2f}",
        f"live_open_positions: {len(positions)}",
        "",
        "--- positions ---",
        format_positions_detail(positions),
    ]
    return "\n".join(lines)


def build_full_portfolio_html(client: PolymarketClient, title_line_html: str) -> str:
    balance = client.get_portfolio_balance(force_allowance_refresh=False)
    positions = client.get_open_positions()
    cash = float(balance.get("cash") or 0)
    pos_val = float(balance.get("positions_market_value") or 0)
    total = float(balance.get("total_value") or (cash + pos_val))
    loc = format_report_local_hhmm()
    parts: List[str] = [
        f"⚡ {title_line_html}",
        f"🕐 <b>Local</b> <code>{loc}</code> <i>({tg_escape(TIMEZONE)})</i>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💵 <b>Cash</b>  ${cash:.2f}",
        f"📈 <b>Positions MTM</b>  ${pos_val:.2f}",
        f"💼 <b>Total</b>  ${total:.2f}",
        f"📊 <b>Open</b>  {len(positions)}",
        "",
    ]
    if not positions:
        parts.append("<i>(no live positions)</i>")
        return "\n".join(parts)
    for index, pos in enumerate(positions, start=1):
        title = tg_escape(pos.get("title") or pos.get("market_id", "?"))
        entry = float(pos.get("avg_price") or 0)
        mark = float(pos.get("cur_price") or 0)
        size = float(pos.get("size") or 0)
        pnl = float(pos.get("cash_pnl") or 0)
        outcome = str(pos.get("outcome") or "").strip()
        oc = f" · <code>{tg_escape(outcome)}</code>" if outcome else ""
        unmapped = ""
        if not pos.get("market_id"):
            unmapped = " ⚠️ <i>no gamma id</i>"
        if pnl > 1e-9:
            pnl_html = f"🟢 <b>+${pnl:.2f}</b>"
        elif pnl < -1e-9:
            pnl_html = f"🔴 <b>${pnl:.2f}</b>"
        else:
            pnl_html = "⚪ $0.00"
        raw_title = pos.get("title") or pos.get("market_id", "?")
        lt = city_local_time_str(str(raw_title))
        lt_html = f"  · 🕐 <code>{lt}</code>" if lt else ""
        parts.append(f"<b>{index}.</b> {title}{unmapped}")
        parts.append(
            f"   📦 <code>{size:.4f}</code>{oc}  ·  entry <code>{entry:.4f}</code>"
            f"  ·  mark <code>{mark:.4f}</code>  ·  {pnl_html}"
            f"  ·  🏠<code>{loc}</code>{lt_html}"
        )
    return "\n".join(parts)


def build_blacklist_html(
    state: Optional[Dict[str, Any]] = None,
    blacklist_ids: Optional[Set[str]] = None,
) -> str:
    if state is None:
        return ""
    bl_ids = blacklist_ids or set()
    rows = collect_blacklist_status_rows(state, bl_ids)
    if not rows:
        return ""
    lines: List[str] = []
    for r in rows:
        mid = str(r.get("market_id") or "")
        title = str(r.get("title") or mid)[:44]
        rem = str(r.get("remaining_label") or "")
        src = str(r.get("source") or "")
        if src == "churn":
            tag = "STOP/CHURN"
        elif src == "day":
            tag = "DAY (UI)"
        else:
            tag = "RUNTIME"
        lines.append(
            f"  <code>{mid}</code> <b>{tag}</b> · <code>{tg_escape(rem)}</code> · "
            f"{tg_escape(title)}"
        )
    return "\n🚫 <b>Blacklist</b> (" + str(len(lines)) + ")\n" + "\n".join(lines)


def send_portfolio_telegram(
    telegram: TelegramBot,
    client: PolymarketClient,
    headline: str,
    *,
    echo_terminal: bool = False,
    terminal_title: str = "portfolio report (telegram echo)",
    headline_html: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None,
    blacklist_ids: Optional[Set[str]] = None,
) -> None:
    plain = build_full_portfolio_plain(client, headline)
    if echo_terminal:
        print_terminal_block(terminal_title, plain)
    if not telegram.is_configured():
        return
    title_h = headline_html if headline_html else f"<b>{tg_escape(headline)}</b>"
    html = build_full_portfolio_html(client, title_h)
    bl_html = build_blacklist_html(state, blacklist_ids)
    if bl_html:
        html += "\n" + bl_html
    telegram.send_html_chunks(html)


def send_daily_report(
    telegram: TelegramBot,
    client: PolymarketClient,
    state: Dict[str, Any],
) -> None:
    now = now_in_report_timezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    tracked = len(state.get("active_trades", {}))
    headline = f"scheduled summary report\ntime={now}\ntracked_state_entries={tracked}"
    hh = (
        f"📋 <b>Scheduled report</b>  <code>{tg_escape(now)}</code>\n"
        f"tracked: <code>{tracked}</code>"
    )
    send_portfolio_telegram(
        telegram,
        client,
        headline,
        headline_html=hh,
        echo_terminal=True,
        terminal_title=f"scheduled report  {now}",
        state=state,
        blacklist_ids=get_effective_settings().blacklist_market_ids,
    )


def should_send_report(state: Dict[str, Any], now: datetime) -> bool:
    if now.hour not in REPORT_HOURS:
        return False
    report_key = now.strftime("%Y-%m-%d-%H")
    return state.get("last_report_sent") != report_key


def should_send_hourly_summary(state: Dict[str, Any], now: datetime) -> bool:
    if now.hour < 7:
        return False
    if now.minute not in (0, 30):
        return False
    slot = now.strftime("%Y-%m-%d-%H-%M")
    return state.get("last_hourly_summary_slot") != slot


def send_hourly_summary_report(
    telegram: TelegramBot,
    client: PolymarketClient,
    state: Dict[str, Any],
    now: datetime,
) -> None:
    ts = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    tracked = len(state.get("active_trades", {}))
    today_label = build_target_day_label(now)
    headline = (
        f"HOURLY · {ts}\n"
        f"target_day_for_new_buys={today_label}\n"
        f"tracked_state_entries={tracked}"
    )
    hh = (
        f"🕐 <b>Hourly portfolio</b>  <code>{tg_escape(ts)}</code>\n"
        f"<i>buys: today’s scan only · exits: all holdings</i>\n"
        f"📅 new buys day: <code>{tg_escape(today_label)}</code>  ·  "
        f"📌 tracked: <code>{tracked}</code>"
    )
    send_portfolio_telegram(
        telegram,
        client,
        headline,
        headline_html=hh,
        echo_terminal=False,
        state=state,
        blacklist_ids=get_effective_settings().blacklist_market_ids,
    )
