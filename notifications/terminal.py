import os
import sys
from typing import Any, Dict, List

from polymarket_client import PolymarketClient

from config.constants import (
    TERM_BOLD,
    TERM_CYAN,
    TERM_DIM,
    TERM_GREEN,
    TERM_RED,
    TERM_RESET,
    TERM_YELLOW,
)
from strategy.time_utils import now_in_report_timezone


def term_colors_enabled() -> bool:
    return bool(sys.stdout.isatty() and not os.environ.get("NO_COLOR", "").strip())


def term_wrap(code: str, text: str) -> str:
    if not term_colors_enabled():
        return text
    return f"{code}{text}{TERM_RESET}"


def term_pnl_label(pnl: float) -> str:
    if pnl > 1e-9:
        return term_wrap(TERM_GREEN, f"+${pnl:.2f}")
    if pnl < -1e-9:
        return term_wrap(TERM_RED, f"${pnl:.2f}")
    return term_wrap(TERM_DIM, f"${pnl:.2f}")


def print_terminal_block(title: str, body: str) -> None:
    line = "=" * 72
    print(f"\n{line}\n{TERM_BOLD}{title}{TERM_RESET}\n{line}\n{body}\n{line}\n")


def print_loop_positions_snapshot(
    client: PolymarketClient, state: Dict[str, Any], label: str
) -> None:
    now = now_in_report_timezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    balance = client.get_portfolio_balance()
    cash = float(balance.get("cash") or 0)
    positions = client.get_open_positions()
    tracked = len(state.get("active_trades", {}))
    header = term_wrap(TERM_CYAN, f"[loop] {label}  {now}")
    pos_val = float(balance.get("positions_market_value") or 0)
    total = float(balance.get("total_value") or (cash + pos_val))
    print(f"\n{header}")
    print(
        f"  cash=${cash:.2f}  positions_mtm=${pos_val:.2f}  portfolio_total=${total:.2f}  "
        f"live_open_positions={len(positions)}  tracked_state_entries={tracked}"
    )
    if not positions:
        print(term_wrap(TERM_DIM, "  (no live positions)"))
        return
    for index, pos in enumerate(positions, start=1):
        title = str(pos.get("title") or pos.get("market_id", "?"))
        if len(title) > 70:
            title = title[:67] + "..."
        pnl = float(pos.get("cash_pnl") or 0)
        mark = float(pos.get("cur_price") or 0)
        size = float(pos.get("size") or 0)
        pnl_s = term_pnl_label(pnl)
        print(f"  {index}. {title}")
        print(f"      shares={size:.4f}  mark@={mark:.4f}  pnl={pnl_s}")


def log_trade_buy_terminal(
    title: str, usd: float, shares: float, probability: float, order_ref: str
) -> None:
    ref = order_ref or "(no ref)"
    msg = (
        f"BUY YES  notional=${usd:.2f}  est_shares~={shares:.4f}  "
        f"yes~={probability:.4f}  ref={ref}\n  {title}"
    )
    print(term_wrap(TERM_CYAN, msg))


def log_trade_sell_terminal(
    title: str,
    reason: str,
    shares: float,
    entry: float,
    mark: float,
    *,
    failed: bool = False,
) -> None:
    if failed:
        print(term_wrap(TERM_RED, f"SELL FAILED ({reason})\n  {title}"))
        return
    est_pnl = (mark - entry) * shares
    color = TERM_GREEN if est_pnl >= -1e-9 else TERM_RED
    if est_pnl > 1e-9:
        est_s = f"+${est_pnl:.2f}"
    else:
        est_s = f"${est_pnl:.2f}"
    msg = (
        f"SELL ({reason})  shares={shares:.4f}  entry~={entry:.4f}  mark~={mark:.4f}  "
        f"est_pnl={est_s}\n  {title}"
    )
    print(term_wrap(color, msg))


def log_trade_claim_terminal(title: str) -> None:
    print(term_wrap(TERM_YELLOW, f"CLAIM\n  {title}"))


def log_limit_sell_posted_terminal(
    title: str,
    reason: str,
    shares: float,
    limit_px: float,
    order_ref: str,
) -> None:
    ref = order_ref or "(no ref)"
    msg = (
        f"LIMIT SELL posted ({reason})  shares={shares:.4f}  limit@={limit_px:.4f}  "
        f"ref={ref}\n  {title}"
    )
    print(term_wrap(TERM_YELLOW, msg))


def log_sleep_terminal(seconds: int) -> None:
    now = now_in_report_timezone().strftime("%H:%M:%S")
    msg = f"[{now}] sleeping {seconds}s until next scan…"
    print(term_wrap(TERM_DIM, msg), flush=True)


def _extract_city_name(question: str) -> str:
    q = question.lower()
    for prefix in ("will the highest temperature in ", "highest temperature in "):
        if q.startswith(prefix):
            rest = q[len(prefix) :]
            for sep in (" be ", " on "):
                idx = rest.find(sep)
                if idx > 0:
                    return rest[:idx].strip().title()
    return ""


def print_scan_summary(
    markets: List[Dict[str, Any]],
    state: Dict[str, Any],
    settings: Any,
    target_day_label: str,
    cash: float,
) -> None:
    """
    compact scan table: header + only interesting rows (held, eligible,
    near-band, blocked) + one-line-per-city summary for the rest.
    """
    from strategy.churn import churn_allows_buy
    from strategy.gates import market_can_post_clob_orders
    from strategy.market_match import active_trade_key_and_row
    from strategy.probability import parse_market_probability
    from strategy.sizing import tradable_cash_usd

    active_trades = state.get("active_trades", {})
    reserve = max(0.0, float(settings.cash_reserve_usd))
    tradable = tradable_cash_usd(cash, settings)
    frac = settings.max_trade_fraction_of_cash
    planned = min(tradable * frac, settings.max_buy_notional_usd, tradable)
    buy_min = settings.buy_min
    buy_max = settings.buy_max
    near_margin = 0.06

    sep = "─" * 100
    print(f"\n{TERM_BOLD}┌{sep}┐{TERM_RESET}")
    print(
        f"{TERM_BOLD}│  SCAN  ·  {target_day_label}  ·  {len(markets)} markets{TERM_RESET}"
    )
    print(
        f"{TERM_BOLD}│  cash=${cash:.2f}  reserve=${reserve:.2f}  "
        f"tradable=${tradable:.2f}  frac={frac:.0%}  "
        f"planned=${planned:.2f}  min_order=${settings.min_order_notional_usd:.2f}  "
        f"cap=${settings.max_buy_notional_usd:.2f}{TERM_RESET}"
    )
    print(
        f"{TERM_BOLD}│  buy_band=[{buy_min:.2f}–{buy_max:.2f}]  "
        f"competition={'ON' if settings.enable_competition_filter else 'OFF'}  "
        f"momentum={'ON' if settings.enable_momentum else 'OFF'}"
        f"{TERM_RESET}"
    )
    print(f"{TERM_BOLD}├{sep}┤{TERM_RESET}")

    if not markets:
        print(f"│  {term_wrap(TERM_YELLOW, '(no markets matched scan filters)')}")
        print(f"{TERM_BOLD}└{sep}┘{TERM_RESET}\n")
        return

    interesting: List[str] = []
    city_best: Dict[str, Dict[str, Any]] = {}

    for market in markets:
        mid = str(market.get("id") or "")
        title = str(market.get("question") or market.get("title") or mid)
        yes_p = parse_market_probability(market)
        clob_ok = market_can_post_clob_orders(market)
        status_raw = str(market.get("status") or "").strip().lower() or "active"
        _, trade_row = active_trade_key_and_row(active_trades, market)
        held = trade_row is not None

        tag = ""
        show = False

        if held:
            entry = float(trade_row.get("entry_price") or 0)
            mark = float(trade_row.get("last_price") or 0)
            pnl_est = (mark - entry) * float(trade_row.get("shares") or 0)
            if pnl_est > 1e-9:
                pnl_s = term_wrap(TERM_GREEN, f"+${pnl_est:.2f}")
            elif pnl_est < -1e-9:
                pnl_s = term_wrap(TERM_RED, f"${pnl_est:.2f}")
            else:
                pnl_s = term_wrap(TERM_DIM, f"${pnl_est:.2f}")
            tag = f"{term_wrap(TERM_CYAN, 'HOLD')}  entry={entry:.2f}  pnl~{pnl_s}"
            show = True
        elif not clob_ok:
            tag = f"{term_wrap(TERM_YELLOW, 'CLOB off')}  ({status_raw})"
            show = True
        elif mid in settings.blacklist_market_ids:
            tag = term_wrap(TERM_YELLOW, "BLACKLISTED")
            show = True
        elif not churn_allows_buy(state, mid):
            tag = term_wrap(TERM_YELLOW, "CHURN cooldown")
            show = True
        elif planned < settings.min_order_notional_usd:
            if buy_min <= yes_p <= buy_max:
                tag = term_wrap(
                    TERM_RED,
                    f"NO CASH  planned=${planned:.2f} < min=${settings.min_order_notional_usd:.2f}",
                )
                show = True
        elif buy_min <= yes_p <= buy_max:
            tag = term_wrap(TERM_GREEN, "ELIGIBLE  → BUY")
            show = True
        elif (buy_min - near_margin) <= yes_p < buy_min:
            tag = term_wrap(TERM_DIM, f"near band  yes={yes_p:.2f} (min={buy_min:.2f})")
            show = True
        elif buy_max < yes_p <= (buy_max + near_margin):
            tag = term_wrap(TERM_DIM, f"near band  yes={yes_p:.2f} (max={buy_max:.2f})")
            show = True

        short_title = title if len(title) <= 70 else title[:67] + "..."
        if show:
            interesting.append(f"│  {yes_p:>5.0%}  {tag}  {short_title}")

        city = _extract_city_name(title)
        if city:
            prev = city_best.get(city)
            if prev is None or yes_p > prev["yes"]:
                city_best[city] = {"yes": yes_p, "title": title, "held": held}

    for line in interesting:
        print(line)

    if interesting:
        print(f"{TERM_BOLD}├{sep}┤{TERM_RESET}")

    skipped_cities = []
    for city in sorted(city_best):
        cb = city_best[city]
        if cb["held"]:
            continue
        if buy_min <= cb["yes"] <= buy_max:
            continue
        if (buy_min - near_margin) <= cb["yes"] <= (buy_max + near_margin):
            continue
        skipped_cities.append(f"{city} {cb['yes']:.0%}")

    if skipped_cities:
        chunk = "│  "
        for i, entry in enumerate(skipped_cities):
            addition = entry + ("  " if i < len(skipped_cities) - 1 else "")
            if len(chunk) + len(addition) > 100:
                print(term_wrap(TERM_DIM, chunk))
                chunk = "│  "
            chunk += addition
        if chunk.strip("│ "):
            print(term_wrap(TERM_DIM, chunk))

    print(f"{TERM_BOLD}└{sep}┘{TERM_RESET}\n", flush=True)
