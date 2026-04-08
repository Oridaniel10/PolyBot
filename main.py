import hashlib
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]

import requests
from dotenv import load_dotenv

from py_clob_client.exceptions import PolyApiException

from polymarket_client import (
    PolymarketClient,
    PolymarketConfig,
    SELL_EXECUTION_LIMIT_GTC,
    SELL_EXECUTION_SKIPPED,
)
from telegram_bot import TelegramBot


STATE_FILE = "state.json"
ENV_FILE = ".env"
TIMEZONE = "Asia/Jerusalem"
REPORT_HOURS = (10, 13, 16, 19)
SCAN_INTERVAL_SECONDS = 60
BUY_MIN_THRESHOLD = 0.55
BUY_MAX_THRESHOLD = 0.70
STOP_LOSS_THRESHOLD = 0.48
TAKE_PROFIT_THRESHOLD = 0.99
DEFAULT_ORDER_SIZE = 10.0
MAX_TRADE_FRACTION_OF_CASH = 0.20
MIN_ORDER_NOTIONAL_USD = 3.0
TELEGRAM_MAX_MESSAGE_LEN = 3800
YES_LABEL = "YES"
STATUS_CLOSED = {"closed", "claimable", "resolved"}
DUST_SHARES_EPS = 1e-6

TERM_RESET = "\033[0m"
TERM_BOLD = "\033[1m"
TERM_DIM = "\033[2m"
TERM_GREEN = "\033[32m"
TERM_RED = "\033[31m"
TERM_YELLOW = "\033[33m"
TERM_CYAN = "\033[36m"


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


def load_env_file(path: str) -> None:
    load_dotenv(Path(path), override=True)


def read_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {"active_trades": {}, "last_report_sent": "", "last_sync_at": ""}
    with open(STATE_FILE, "r", encoding="utf-8") as state_file:
        try:
            return json.load(state_file)
        except json.JSONDecodeError:
            return {"active_trades": {}, "last_report_sent": "", "last_sync_at": ""}


def write_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2, ensure_ascii=True)


def parse_market_probability(market: Dict[str, Any]) -> float:
    probability_fields = ("lastTradePrice", "price", "probability", "yesPrice")
    for field in probability_fields:
        value = market.get(field)
        if value is None:
            continue
        try:
            parsed = float(value)
            return parsed if parsed <= 1 else parsed / 100.0
        except (TypeError, ValueError):
            continue
    return 0.0


def market_status(market: Dict[str, Any]) -> str:
    return str(market.get("status", "")).strip().lower()


def market_can_post_clob_orders(market: Dict[str, Any]) -> bool:
    status = market_status(market)
    if status in STATUS_CLOSED:
        return False
    if market.get("archived") is True:
        return False
    if market.get("acceptingOrders") is False:
        return False
    if market.get("enableOrderBook") is False:
        return False
    if market.get("active") is False:
        return False
    if market.get("suspended") is True:
        return False
    blocked = (
        "in review",
        "in_review",
        "pending review",
        "paused",
        "halted",
        "suspended",
        "disputed",
    )
    for frag in blocked:
        if frag in status:
            return False
    return True


def now_in_report_timezone() -> datetime:
    if ZoneInfo is None:
        return datetime.utcnow()
    return datetime.now(ZoneInfo(TIMEZONE))


def build_target_day_label(now: datetime) -> str:
    return f"{now.strftime('%B')} {now.day}"


def max_trade_fraction() -> float:
    raw = os.getenv("MAX_TRADE_FRACTION_OF_CASH", "").strip()
    if raw:
        try:
            return min(1.0, max(0.0, float(raw)))
        except ValueError:
            pass
    return MAX_TRADE_FRACTION_OF_CASH


def min_order_notional() -> float:
    raw = os.getenv("MIN_ORDER_NOTIONAL_USD", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return MIN_ORDER_NOTIONAL_USD


def compute_buy_usd_amount(cash_usd: float, yes_price: float) -> float:
    if cash_usd <= 0 or yes_price <= 0 or yes_price >= 1.0:
        return 0.0
    frac = max_trade_fraction()
    max_usd = min(cash_usd * frac, cash_usd)
    if max_usd < min_order_notional():
        return 0.0
    return max(0.0, round(max_usd, 6))


def order_ref_from_response(order: Any) -> str:
    if not isinstance(order, dict):
        return ""
    nested = order.get("order")
    if isinstance(nested, dict):
        inner = str(
            nested.get("id") or nested.get("orderID") or nested.get("orderId") or ""
        )
        if inner:
            return inner
    return str(
        order.get("orderID")
        or order.get("orderId")
        or order.get("id")
        or order.get("oid")
        or order.get("transactionHash")
        or ""
    )


def send_telegram_chunks(telegram: TelegramBot, text: str) -> None:
    if not telegram.is_configured():
        return
    chunk_size = TELEGRAM_MAX_MESSAGE_LEN
    for start in range(0, len(text), chunk_size):
        chunk = text[start : start + chunk_size]
        try:
            telegram.send_message(chunk)
        except requests.RequestException:
            break


def format_positions_detail(positions: List[Dict[str, Any]]) -> str:
    if not positions:
        return "(no live open positions — settled or flat rows are hidden)"
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
            f"   shares={size:.4f}{oc}  bought@={entry:.4f}  mark@={mark:.4f}  pnl=${pnl:.2f}{unmapped}"
        )
    return "\n".join(lines)


def build_full_portfolio_message(client: PolymarketClient, headline: str) -> str:
    balance = client.get_portfolio_balance()
    positions = client.get_open_positions()
    cash = float(balance.get("cash") or 0)
    pos_val = float(balance.get("positions_market_value") or 0)
    total = float(balance.get("total_value") or (cash + pos_val))
    lines = [
        headline.strip(),
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


def send_portfolio_telegram(
    telegram: TelegramBot,
    client: PolymarketClient,
    headline: str,
    *,
    echo_terminal: bool = False,
    terminal_title: str = "portfolio report (telegram echo)",
) -> None:
    message = build_full_portfolio_message(client, headline)
    if echo_terminal:
        print_terminal_block(terminal_title, message)
    send_telegram_chunks(telegram, message)


def send_daily_report(
    telegram: TelegramBot,
    client: PolymarketClient,
    state: Dict[str, Any],
) -> None:
    now = now_in_report_timezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    tracked = len(state.get("active_trades", {}))
    headline = f"scheduled summary report\ntime={now}\ntracked_state_entries={tracked}"
    send_portfolio_telegram(
        telegram,
        client,
        headline,
        echo_terminal=True,
        terminal_title=f"scheduled report  {now}",
    )


def should_send_report(state: Dict[str, Any], now: datetime) -> bool:
    if now.hour not in REPORT_HOURS:
        return False
    report_key = now.strftime("%Y-%m-%d-%H")
    return state.get("last_report_sent") != report_key


def normalize_positions(positions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    normalized: Dict[str, Dict[str, Any]] = {}
    for position in positions:
        market_id = str(position.get("market_id") or position.get("marketId") or "")
        if not market_id:
            continue
        normalized[market_id] = position
    return normalized


def sync_state_with_portfolio(
    client: PolymarketClient, state: Dict[str, Any]
) -> Dict[str, Any]:
    positions = client.get_open_positions()
    normalized_positions = normalize_positions(positions)
    tracked = state.get("active_trades", {})

    active_trades: Dict[str, Any] = {}
    for market_id, details in tracked.items():
        if market_id in normalized_positions:
            active_trades[market_id] = details

    for market_id, position in normalized_positions.items():
        prev = active_trades.get(market_id, {})
        size = float(position.get("size", 0.0) or 0.0)
        avg_px = float(position.get("avg_price", 0.0) or 0.0)
        cur_px = float(position.get("cur_price", 0.0) or 0.0)
        row = {
            "market_id": market_id,
            "shares": size,
            "last_action": prev.get("last_action", "sync"),
            "entry_price": avg_px or float(prev.get("entry_price", 0.0) or 0.0),
            "last_price": cur_px if cur_px else avg_px,
            "order_ref": prev.get("order_ref", ""),
        }
        pend = prev.get("pending_limit_sell_order_id")
        if pend:
            row["pending_limit_sell_order_id"] = pend
            if prev.get("pending_limit_sell_price") is not None:
                row["pending_limit_sell_price"] = prev.get("pending_limit_sell_price")
        active_trades[market_id] = row

    state["active_trades"] = active_trades
    state["last_sync_at"] = now_in_report_timezone().isoformat()
    return state


def place_buy(
    client: PolymarketClient,
    market: Dict[str, Any],
    state: Dict[str, Any],
    telegram: TelegramBot,
    probability: float,
) -> None:
    market_id = str(market.get("id") or "")
    if not market_id:
        return
    title = (
        market.get("question")
        or market.get("title")
        or market.get("id", "unknown-market")
    )
    balance_before = client.get_portfolio_balance()
    cash = float(balance_before.get("cash") or 0)
    usd = compute_buy_usd_amount(cash, probability)
    if usd <= 0:
        return
    try:
        order = client.place_market_buy_yes(market, usd)
    except PolyApiException as err:
        print(term_wrap(TERM_RED, f"BUY FAILED: {err}\n  {title}"))
        try:
            telegram.send_message(f"TRADE FAILED (buy): {err}")
        except requests.RequestException:
            pass
        return
    except Exception as err:
        print(term_wrap(TERM_RED, f"BUY FAILED: {err!r}\n  {title}"))
        try:
            telegram.send_message(f"TRADE FAILED (buy): {err!r}")
        except requests.RequestException:
            pass
        return
    shares = round(usd / probability, 6) if probability else 0.0
    active_trades = state.setdefault("active_trades", {})
    active_trades[market_id] = {
        "market_id": market_id,
        "shares": shares,
        "last_action": "buy",
        "entry_price": probability,
        "last_price": probability,
        "order_ref": order_ref_from_response(order),
    }
    frac = max_trade_fraction()
    headline = (
        "TRADE: BUY YES\n"
        f"{title}\n"
        f"notional_usd={usd:.2f}  est_shares~={shares:.6f}  yes_price~={probability:.4f}\n"
        f"order_cap={frac * 100:.0f}% of cash (max ${cash * frac:.2f} notional this trade)\n"
        f"order_ref={order_ref_from_response(order) or order}"
    )
    log_trade_buy_terminal(
        str(title),
        usd,
        shares,
        probability,
        order_ref_from_response(order),
    )
    try:
        send_portfolio_telegram(telegram, client, headline)
    except Exception as err:
        print(term_wrap(TERM_RED, f"buy ok but telegram failed: {err!r}"))


def close_position(
    client: PolymarketClient,
    market: Dict[str, Any],
    state: Dict[str, Any],
    telegram: TelegramBot,
    probability: float,
    reason: str,
) -> None:
    market_id = str(market.get("id") or "")
    active_trades = state.get("active_trades", {})
    trade = active_trades.get(market_id)
    if not market_id or not trade:
        return
    if trade.get("pending_limit_sell_order_id"):
        return

    shares_state = float(trade.get("shares", DEFAULT_ORDER_SIZE) or DEFAULT_ORDER_SIZE)
    title = (
        market.get("question")
        or market.get("title")
        or market.get("id", "unknown-market")
    )
    entry = float(trade.get("entry_price", trade.get("last_price", 0)) or 0)
    exchange_yes = client.get_yes_shares_on_exchange_for_market_id(market_id)
    if exchange_yes < DUST_SHARES_EPS:
        active_trades.pop(market_id, None)
        msg = (
            "SELL N/A: no YES size on exchange (flat / resolved / not in data-api)\n"
            f"tracked_state_shares={shares_state:.6f}\n"
            f"{title}"
        )
        print(term_wrap(TERM_YELLOW, msg))
        try:
            telegram.send_message(msg)
        except requests.RequestException:
            pass
        return

    sell_shares = min(shares_state, exchange_yes)
    if sell_shares + 1e-12 < shares_state:
        print(
            term_wrap(
                TERM_DIM,
                f"[sell] clamping state shares {shares_state:.4f} -> exchange {sell_shares:.4f}",
            )
        )

    try:
        result = client.place_market_sell_yes(
            market, sell_shares, reference_price=probability
        )
    except PolyApiException as err:
        fp = hashlib.sha256(str(err).encode("utf-8", errors="replace")).hexdigest()
        if trade.get("_sell_err_fingerprint") == fp:
            print(
                term_wrap(
                    TERM_DIM,
                    "[sell] same error as last tick — telegram suppressed",
                )
            )
            return
        trade["_sell_err_fingerprint"] = fp
        log_trade_sell_terminal(
            str(title), reason, sell_shares, entry, probability, failed=True
        )
        detail = str(err.error_msg) if err.error_msg is not None else str(err)
        print(term_wrap(TERM_RED, f"  detail: {err}\n  {detail}"))
        try:
            telegram.send_message(
                "TRADE FAILED (sell)\n"
                f"{title}\n"
                f"reason={reason}  attempted_shares={sell_shares:.6f}  "
                f"exchange_yes~={exchange_yes:.6f}  mark~={probability:.4f}\n"
                f"---\n{detail}"
            )
        except requests.RequestException:
            pass
        return
    except Exception as err:
        fp = hashlib.sha256(str(err).encode("utf-8", errors="replace")).hexdigest()
        if trade.get("_sell_err_fingerprint") == fp:
            print(
                term_wrap(
                    TERM_DIM,
                    "[sell] same error as last tick — telegram suppressed",
                )
            )
            return
        trade["_sell_err_fingerprint"] = fp
        log_trade_sell_terminal(
            str(title), reason, sell_shares, entry, probability, failed=True
        )
        print(term_wrap(TERM_RED, f"  detail: {err!r}"))
        try:
            telegram.send_message(
                "TRADE FAILED (sell)\n"
                f"{title}\n"
                f"reason={reason}  attempted_shares={sell_shares:.6f}  "
                f"exchange_yes~={exchange_yes:.6f}  mark~={probability:.4f}\n"
                f"---\n{err!r}"
            )
        except requests.RequestException:
            pass
        return

    trade.pop("_sell_err_fingerprint", None)

    if result.get("sell_execution") == SELL_EXECUTION_SKIPPED:
        active_trades.pop(market_id, None)
        body = result.get("sell_attempt_summary") or ""
        msg = (
            f"SELL SKIPPED: {result.get('skip_reason')}\n"
            f"{title}\n"
            f"attempted_shares={result.get('attempted_shares')}  "
            f"clob_min_order_size={result.get('min_order_size')}\n"
            f"(removed from bot state — dust / cannot post on CLOB)\n"
            f"---\n{body}"
        )
        print(term_wrap(TERM_YELLOW, msg))
        try:
            telegram.send_message(msg)
        except requests.RequestException:
            pass
        return
    if result.get("sell_execution") == SELL_EXECUTION_LIMIT_GTC:
        limit_px = float(result.get("limit_price") or 0)
        oid = order_ref_from_response(result)
        if not oid:
            print(
                term_wrap(
                    TERM_RED,
                    f"LIMIT SELL response missing order id — check exchange UI\n  {title}",
                )
            )
            try:
                telegram.send_message(
                    f"TRADE WARN: limit sell posted but no order id in API response\n{title}"
                )
            except requests.RequestException:
                pass
            return
        trade["pending_limit_sell_order_id"] = oid
        trade["pending_limit_sell_price"] = limit_px
        summary = result.get("sell_attempt_summary") or ""
        headline = (
            f"TRADE: LIMIT SELL posted ({reason})\n"
            f"{title}\n"
            f"shares={sell_shares:.6f}  limit@={limit_px:.4f}  mark~={probability:.4f}\n"
            f"order_ref={oid or result}\n"
            f"---\n{summary}"
        )
        log_limit_sell_posted_terminal(str(title), reason, sell_shares, limit_px, oid)
        try:
            send_portfolio_telegram(telegram, client, headline)
        except Exception as err:
            print(
                term_wrap(TERM_RED, f"limit sell posted but telegram failed: {err!r}")
            )
        return

    active_trades.pop(market_id, None)
    summary = result.get("sell_attempt_summary") or ""
    headline = (
        f"TRADE: SELL ({reason})\n"
        f"{title}\n"
        f"shares={sell_shares:.6f}  entry~={entry:.4f}  mark~={probability:.4f}\n"
        f"---\n{summary}"
    )
    log_trade_sell_terminal(
        str(title), reason, sell_shares, entry, probability, failed=False
    )
    try:
        send_portfolio_telegram(telegram, client, headline)
    except Exception as err:
        print(term_wrap(TERM_RED, f"sell ok but telegram failed: {err!r}"))


def claim_position(
    client: PolymarketClient,
    market: Dict[str, Any],
    state: Dict[str, Any],
    telegram: TelegramBot,
) -> None:
    market_id = str(market.get("id") or "")
    if not market_id:
        return
    title = (
        market.get("question")
        or market.get("title")
        or market.get("id", "unknown-market")
    )
    try:
        client.claim_market(market_id)
    except Exception as err:
        print(term_wrap(TERM_RED, f"CLAIM FAILED: {err!r}\n  {title}"))
        try:
            telegram.send_message(f"TRADE FAILED (claim): {err!r}")
        except requests.RequestException:
            pass
        return
    state.get("active_trades", {}).pop(market_id, None)
    headline = f"TRADE: CLAIM\n{title}"
    log_trade_claim_terminal(str(title))
    try:
        send_portfolio_telegram(telegram, client, headline)
    except Exception as err:
        print(term_wrap(TERM_RED, f"claim ok but telegram failed: {err!r}"))


def run_once(
    client: PolymarketClient, telegram: TelegramBot, state: Dict[str, Any]
) -> Dict[str, Any]:
    now = now_in_report_timezone()
    target_day_label = build_target_day_label(now)
    markets = client.get_highest_temperature_markets(target_day_label=target_day_label)
    active_trades = state.setdefault("active_trades", {})
    client.flush_pending_limit_sells(active_trades)

    for market in markets:
        market_id = str(market.get("id") or "")
        if not market_id:
            continue

        probability = parse_market_probability(market)
        status = market_status(market)
        has_position = market_id in active_trades

        if has_position and status in STATUS_CLOSED:
            claim_position(client, market, state, telegram)
            continue

        if has_position and not market_can_post_clob_orders(market):
            tr = active_trades[market_id]
            if not tr.get("_warned_clob_disabled"):
                t = (
                    market.get("question")
                    or market.get("title")
                    or market.get("id", "unknown-market")
                )
                warn = (
                    "EXIT PAUSED: market not accepting CLOB orders (review / paused / "
                    f"book off)\n{t}\nstatus={status!r}\n"
                    f"acceptingOrders={market.get('acceptingOrders')}  "
                    f"enableOrderBook={market.get('enableOrderBook')}  "
                    f"active={market.get('active')}"
                )
                print(term_wrap(TERM_YELLOW, warn))
                try:
                    telegram.send_message(warn)
                except requests.RequestException:
                    pass
                tr["_warned_clob_disabled"] = True
            continue

        if has_position:
            active_trades[market_id].pop("_warned_clob_disabled", None)

        if has_position and (probability < STOP_LOSS_THRESHOLD):
            close_position(client, market, state, telegram, probability, "stop-loss")
            continue

        if has_position and (probability > TAKE_PROFIT_THRESHOLD):
            close_position(client, market, state, telegram, probability, "take-profit")
            continue

        if (
            (not has_position)
            and BUY_MIN_THRESHOLD <= probability <= BUY_MAX_THRESHOLD
            and market_can_post_clob_orders(market)
        ):
            place_buy(client, market, state, telegram, probability)

    return state


def build_config() -> PolymarketConfig:
    sig_env = os.getenv("POLY_SIGNATURE_TYPE", "").strip()
    clob_sig = int(sig_env) if sig_env.isdigit() else None
    return PolymarketConfig(
        api_key=os.getenv("API_KEY", os.getenv("POLY_API_KEY", "")),
        api_secret=os.getenv("API_SECRET", ""),
        api_passphrase=os.getenv("API_PASSPHRASE", ""),
        private_key=os.getenv("POLY_PRIVATE_KEY", os.getenv("Wallet_PRIVATE_KEY", "")),
        proxy_address=os.getenv("POLY_PROXY_ADDRESS", os.getenv("POLY_ADDRESS", "")),
        gamma_base_url=os.getenv(
            "POLY_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"
        ),
        clob_base_url=os.getenv("POLY_CLOB_BASE_URL", "https://clob.polymarket.com"),
        clob_signature_type=clob_sig,
    )


def run_bot() -> None:
    load_env_file(ENV_FILE)
    config = build_config()
    telegram = TelegramBot(
        token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
    client = PolymarketClient(config)
    state = read_state()
    try:
        state = sync_state_with_portfolio(client, state)
        write_state(state)
    except Exception as err:
        print(term_wrap(TERM_RED, f"[startup sync failed] {err!r}"))
    try:
        send_portfolio_telegram(
            telegram,
            client,
            "bot started — synced portfolio (cash + positions)",
            echo_terminal=True,
            terminal_title="bot started — portfolio snapshot",
        )
    except Exception as err:
        print(term_wrap(TERM_RED, f"[startup portfolio message failed] {err!r}"))

    while True:
        now = now_in_report_timezone()
        try:
            print_loop_positions_snapshot(client, state, "tick start")
        except Exception as err:
            print(term_wrap(TERM_RED, f"[snapshot failed] {err!r}"))
        try:
            state = run_once(client, telegram, state)
            if should_send_report(state, now):
                send_daily_report(telegram, client, state)
                state["last_report_sent"] = now.strftime("%Y-%m-%d-%H")
            write_state(state)
        except Exception as error:
            print(term_wrap(TERM_RED, f"[loop error] {error!r}"))
            try:
                telegram.send_message(f"bot error: {error!r}")
            except requests.RequestException:
                pass
        log_sleep_terminal(SCAN_INTERVAL_SECONDS)
        time.sleep(SCAN_INTERVAL_SECONDS)


def main() -> None:
    run_bot()


if __name__ == "__main__":
    main()
