import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from polymarket_client import PolymarketClient

from config import constants as C
from config.constants import REPORT_HOURS, TIMEZONE
from config.settings import get_effective_settings
from forecast.parse_title import BracketKind, parse_highest_temp_title
from notifications.forecast_cache_fmt import portfolio_position_forecast_html
from notifications.terminal import print_terminal_block
from strategy.blacklist_display import collect_blacklist_status_rows
from strategy.city_tz import city_local_datetime_now_str, city_local_time_str
from strategy.time_utils import (
    build_target_day_label,
    format_report_local_hhmm,
    now_in_report_timezone,
)
from telegram_bot import TELEGRAM_SEND_MESSAGE_MAX, TelegramBot, tg_escape


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


def _momentum_label(change: float) -> str:
    if change > 0.05:
        return f"📈<code>+{change:.1%}</code>"
    if change < -0.05:
        return f"📉<code>{change:.1%}</code>"
    return f"➡️<code>{change:+.1%}</code>"


def _mom_plain_pct(change: float) -> str:
    return f"{change * 100.0:+.1f}%"


def _momentum_two_windows_html(market_id: str) -> str:
    """15m + rolling 2h YES change from local price_samples (display only)."""
    mid = str(market_id or "").strip()
    if not mid:
        return ""
    try:
        from strategy.momentum_engine import price_change_in_window

        _, _, m15 = price_change_in_window(mid, C.MOMENTUM_WINDOW_SECONDS)
        _, _, m2h = price_change_in_window(mid, C.PORTFOLIO_TELEGRAM_MOMENTUM_LONG_SEC)
    except Exception:
        return ""
    return (
        f"   ⚡ <b>Momentum</b> 15m {_momentum_label(m15)} · "
        f"2h roll {_momentum_label(m2h)}"
    )


def _trade_row_for_market(
    state: Optional[Dict[str, Any]], market_id: str
) -> Optional[Dict[str, Any]]:
    if not state or not market_id:
        return None
    at = state.get("active_trades")
    if not isinstance(at, dict):
        return None
    row = at.get(str(market_id).strip())
    return row if isinstance(row, dict) else None


def _entry_ts_from_trade(trade: Dict[str, Any]) -> Optional[float]:
    raw = trade.get("entry_time_utc")
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            return float(raw)
        s = str(raw).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError, OSError):
        return None


def _trade_row_for_time_decay(
    trade: Dict[str, Any], avg_entry: float
) -> Dict[str, Any]:
    row = dict(trade)
    if float(row.get("entry_price") or 0) <= 1e-9 and avg_entry > 1e-9:
        row["entry_price"] = float(avg_entry)
    return row


def _time_decay_status_html(
    trade: Dict[str, Any], mark: float, avg_entry: float
) -> str:
    """same gates as should_time_decay_exit (hours since bot entry, gain vs entry, mark)."""
    et = _entry_ts_from_trade(trade)
    if et is None:
        return ""
    try:
        from strategy.time_filter import should_time_decay_exit

        row = _trade_row_for_time_decay(trade, avg_entry)
        would, _reason = should_time_decay_exit(
            row,
            float(mark),
            decay_hours=float(C.TIME_DECAY_HOURS),
            min_gain_pct=float(C.TIME_DECAY_MIN_GAIN),
            max_price_for_decay=float(C.TIME_DECAY_MAX_PRICE),
        )
    except Exception:
        return ""
    hours = (time.time() - et) / 3600.0
    ep = float(row.get("entry_price") or 0)
    gain = ((mark - ep) / ep) if ep > 1e-9 else 0.0
    if would:
        tag = "<b>WOULD EXIT</b> <i>(time_decay)</i>"
    else:
        tag = "<i>no time_decay</i>"
    return (
        f"   ⏰ <b>Time decay</b> held <code>{hours:.2f}h</code> "
        f"(need ≥{C.TIME_DECAY_HOURS:g}h) · gain vs entry {_momentum_label(gain)} "
        f"(need &lt;{C.TIME_DECAY_MIN_GAIN:.0%}) · mark <code>{mark:.3f}</code> "
        f"(need mark &lt;{C.TIME_DECAY_MAX_PRICE:.2f}) → {tag}"
    )


def _yes_delta_entry_window_html(market_id: str, entry_ts: float) -> str:
    """
    first→last YES in samples from max(entry, now−2h) to now (same horizon as decay display).
    """
    mid = str(market_id or "").strip()
    if not mid or entry_ts <= 0:
        return ""
    try:
        from strategy.momentum import load_samples_for_market
    except Exception:
        return ""
    now_ts = time.time()
    tw = float(C.PORTFOLIO_TELEGRAM_MOMENTUM_LONG_SEC)
    try:
        series_all = load_samples_for_market(mid, tw + 120.0, now_ts)
    except Exception:
        return ""
    t_start = max(float(entry_ts), now_ts - tw)
    series = [(t, y) for t, y in series_all if t >= t_start - 1e-6]
    if len(series) < 2:
        return (
            "   📌 <b>YES Δ (since entry, ≤2h)</b> <i>not enough samples in window</i>"
        )
    old_y, new_y = float(series[0][1]), float(series[-1][1])
    if old_y <= 1e-9:
        return ""
    roc = (new_y - old_y) / old_y
    return (
        f"   📌 <b>YES Δ (since entry, ≤2h)</b> {_momentum_label(roc)} "
        f"<i>window</i> <code>{old_y:.3f}</code>→<code>{new_y:.3f}</code>"
    )


def _bucket_short_label(question_title: str) -> str:
    p = parse_highest_temp_title(str(question_title))
    if not p:
        s = (question_title or "").strip()
        return s[:28] if s else "?"
    if p.bracket == BracketKind.AT_MOST:
        return f"≤{p.threshold_c:g}°C"
    if p.bracket == BracketKind.AT_LEAST:
        return f"≥{p.threshold_c:g}°C"
    return f"{p.threshold_c:g}°C"


def _event_board_top4_html(
    market_id: str,
    market_title: str,
    client: PolymarketClient,
    event_cache: Dict[str, Any],
    *,
    held_yes_mark: Optional[float] = None,
) -> str:
    """Top 4 sibling buckets by YES mark, 15m + 2h momentum each (needs Gamma market for event id)."""
    try:
        from polymarket_client import gamma_event_ids_for_market
        from strategy.momentum_engine import price_change_in_window
        from strategy.probability import parse_market_probability

        if not parse_highest_temp_title(str(market_title)):
            return ""
        mid = str(market_id or "").strip()
        gamma_m = client.get_market_by_id(mid) if mid else None
        merged: Dict[str, Any] = dict(gamma_m) if isinstance(gamma_m, dict) else {}
        if mid:
            merged["id"] = mid
        merged["question"] = market_title
        eids = gamma_event_ids_for_market(merged)
        if not eids:
            return ""
        eid = str(eids[0]).strip()
        if eid not in event_cache:
            event_cache[eid] = client.get_markets_for_event_id(eid)
        siblings = event_cache.get(eid) or []
        if len(siblings) < 2:
            return ""
        rows: List[Tuple[str, float, str]] = []
        hy = float(held_yes_mark or 0.0)
        for m in siblings:
            smid = str(m.get("id") or "").strip()
            qt = str(m.get("question") or m.get("title") or "")
            p = parse_market_probability(m)
            if mid and smid == mid and hy > 1e-9:
                p = hy
            rows.append((smid, p, qt))
        rows.sort(key=lambda x: -x[1])
        top4 = rows[:4]
        lines: List[str] = [
            "      <b>Live competition by mark</b> <i>(top 4 · high→low · mom 15m/2h)</i>",
        ]
        for rank, (smid, p, qt) in enumerate(top4, start=1):
            _, _, mom15 = price_change_in_window(smid, C.MOMENTUM_WINDOW_SECONDS)
            _, _, mom2h = price_change_in_window(
                smid, C.PORTFOLIO_TELEGRAM_MOMENTUM_LONG_SEC
            )
            lab = _bucket_short_label(qt)
            you = " <b>YOU</b>" if mid and smid == mid else ""
            s15 = _mom_plain_pct(mom15)
            s2h = _mom_plain_pct(mom2h)
            lines.append(
                f"      <code>#{rank}</code> <b>{tg_escape(lab)}</b>{you} · "
                f"mark <code>{p:.2f}</code> · "
                f"15m <code>{tg_escape(s15)}</code> · 2h <code>{tg_escape(s2h)}</code>"
            )
        return "\n".join(lines)
    except Exception:
        return ""


def build_full_portfolio_html(
    client: PolymarketClient,
    title_line_html: str,
    state: Optional[Dict[str, Any]] = None,
) -> str:
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
        f"💵 <b>Cash</b>  <code>${cash:.2f}</code>",
        f"📈 <b>Positions MTM</b>  <code>${pos_val:.2f}</code>",
        f"💼 <b>Total</b>  <code>${total:.2f}</code>",
        f"📊 <b>Open</b>  <code>{len(positions)}</code>",
        "",
    ]
    if not positions:
        parts.append("<i>(no live positions)</i>")
        return "\n".join(parts)
    event_cache: Dict[str, Any] = {}
    for index, pos in enumerate(positions, start=1):
        raw_title = str(pos.get("title") or pos.get("market_id", "?"))
        title = tg_escape(raw_title)
        mid = str(pos.get("market_id") or "").strip()
        entry = float(pos.get("avg_price") or 0)
        mark = float(pos.get("cur_price") or 0)
        size = float(pos.get("size") or 0)
        pnl = float(pos.get("cash_pnl") or 0)
        outcome = str(pos.get("outcome") or "").strip()
        oc = f" · <code>{tg_escape(outcome)}</code>" if outcome else ""
        unmapped = " ⚠️ <i>no gamma id</i>" if not mid else ""

        if pnl > 1e-9:
            pnl_html = f"🟢 <b>+${pnl:.2f}</b>"
        elif pnl < -1e-9:
            pnl_html = f"🔴 <b>${pnl:.2f}</b>"
        else:
            pnl_html = "⚪ $0.00"

        pnl_pct = ((mark - entry) / entry * 100) if entry > 1e-9 else 0.0
        pnl_pct_html = f"<code>({pnl_pct:+.1f}%)</code>"

        local_full = city_local_datetime_now_str(raw_title)
        lt = city_local_time_str(raw_title)
        if local_full:
            lt_html = f" · 📍 <i>Local now</i> <code>{tg_escape(local_full)}</code>"
        elif lt:
            lt_html = f" · 🕐 <code>{lt}</code> <i>local</i>"
        else:
            lt_html = ""

        if index > 1:
            parts.append("─────────────────────")
        parts.append(f"<u><b>{index}.</b> {title}{unmapped}</u>")
        parts.append(
            f"   📦 <code>{size:.3f}</code>{oc} · entry <code>{entry:.3f}</code>"
            f" · mark <code>{mark:.3f}</code>"
        )
        parts.append(f"   {pnl_html} {pnl_pct_html}{lt_html}")

        # forecast + calibration + research edge
        fc = portfolio_position_forecast_html(
            mid,
            raw_title,
            mark if mark > 1e-9 else None,
        )
        if fc:
            parts.append(fc)

        # same-event board (top 4 YES + mom each); else single-line mom for this market
        board_html = ""
        if mid:
            board_html = _event_board_top4_html(
                mid,
                raw_title,
                client,
                event_cache,
                held_yes_mark=mark if mark > 1e-9 else None,
            )
        if board_html:
            parts.append(board_html)
        else:
            if mid:
                mom_line = _momentum_two_windows_html(mid)
                if mom_line:
                    parts.append(mom_line)
        tr = _trade_row_for_market(state, mid)
        if tr and mid:
            td = _time_decay_status_html(tr, mark, entry)
            if td:
                parts.append(td)
            ets = _entry_ts_from_trade(tr)
            if ets is not None:
                yd = _yes_delta_entry_window_html(mid, float(ets))
                if yd:
                    parts.append(yd)

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
    footer_plain: Optional[str] = None,
) -> None:
    plain = build_full_portfolio_plain(client, headline)
    if footer_plain:
        plain = plain.strip() + "\n\n" + footer_plain.strip()
    if echo_terminal:
        print_terminal_block(terminal_title, plain)
    if not telegram.is_configured():
        return
    title_h = headline_html if headline_html else f"<b>{tg_escape(headline)}</b>"
    html = build_full_portfolio_html(client, title_h, state=state)
    bl_html = build_blacklist_html(state, blacklist_ids)
    if bl_html:
        html += "\n" + bl_html
    telegram.send_html_chunks(html)
    if footer_plain:
        body = footer_plain.strip()
        head = "📋 Strategy settings digest (startup)\n\n"
        total = len(head) + len(body)
        if total <= TELEGRAM_SEND_MESSAGE_MAX:
            try:
                telegram.send_message(head + body)
            except Exception as exc:
                print(f"[telegram] strategy digest (plain) failed: {exc!r}", flush=True)
        else:
            ok = telegram.send_document_text(
                "startup_strategy_digest.txt",
                body,
                caption="📋 Strategy settings digest (startup) — full list attached",
            )
            if not ok:
                telegram.send_plain_by_lines(head + body)


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


def maybe_send_portfolio_status_heartbeat(
    telegram: TelegramBot,
    client: PolymarketClient,
    state: Dict[str, Any],
) -> bool:
    """
    sends full portfolio STATUS at least every PORTFOLIO_STATUS_HEARTBEAT_SEC (default 1h).

    returns:
    - true if telegram was sent (caller should write_state).
    """
    if not telegram.is_configured():
        return False
    now_ts = time.time()
    last = float(state.get("last_status_heartbeat_ts") or 0.0)
    if now_ts - last < float(C.PORTFOLIO_STATUS_HEARTBEAT_SEC):
        return False
    loc = format_report_local_hhmm()
    send_portfolio_telegram(
        telegram,
        client,
        f"STATUS · heartbeat {loc}",
        headline_html=(
            "<b>📊 STATUS</b> <i>· every 1h</i>\n"
            f"<code>{tg_escape(loc)}</code> <i>(Asia/Jerusalem)</i>"
        ),
        state=state,
        blacklist_ids=get_effective_settings().blacklist_market_ids,
    )
    state["last_status_heartbeat_ts"] = now_ts
    return True


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
