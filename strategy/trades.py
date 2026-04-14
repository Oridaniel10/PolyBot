import hashlib
import os
import time
from typing import Any, Dict, List, Optional

from config.constants import BUY_EARLIEST_HOUR, MAX_CONCURRENT_POSITIONS

import requests
from py_clob_client.exceptions import PolyApiException

from config.constants import (
    DEFAULT_ORDER_SIZE,
    DUST_SHARES_EPS,
    SELL_BELOW_MIN_COOLDOWN_SEC,
    SELL_BYPASS_MIN_COOLDOWN_REASONS,
    STATUS_CLOSED,
    TAKE_PROFIT_COMPARE_SLACK,
    TERM_DIM,
    TERM_RED,
    TERM_YELLOW,
)
from config.settings import RuntimeSettings
from notifications.portfolio import send_portfolio_telegram
from notifications.telegram_fmt import (
    format_buy_max_risk_line_html,
    format_est_pnl_line_html,
)
from notifications.terminal import (
    log_limit_sell_posted_terminal,
    log_trade_buy_terminal,
    log_trade_claim_terminal,
    log_trade_sell_terminal,
    term_wrap,
)
from polymarket_client import (
    PolymarketClient,
    SELL_EXECUTION_LIMIT_GTC,
    SELL_EXECUTION_SKIPPED,
    gamma_event_ids_for_market,
)
from state.pnl_ledger import append_ledger_row, append_trade_csv_row
from strategy.city_tz import _extract_city_from_title
from strategy.churn import (
    churn_on_stop_loss_exit,
    churn_on_take_profit,
    churn_allows_buy,
)
from strategy.competition import passes_competition_lead_gap
from strategy.gates import market_can_post_clob_orders, market_status
from strategy.market_match import (
    active_trade_key_and_row,
    trade_row_matches_gamma_market,
    warn_gamma_trade_mismatch_once,
)
from strategy.momentum import (
    max_peer_yes_drop_ratio,
    momentum_effective_buy_max,
    momentum_entry_allowed,
)
from strategy.probability import (
    parse_market_probability,
    stop_loss_reference_if_triggered,
    take_profit_decision_probability,
)
from forecast.forecast_service import get_forecast_max_for_city_day
from forecast.parse_title import (
    forecast_contradicts_strongly,
    forecast_supports_yes,
    parse_highest_temp_title,
)
from strategy.flow_signals import flow_peer_recommends_exit
from strategy.sizing import compute_buy_usd_amount, planned_buy_cap_lines
from strategy.time_utils import format_report_local_hhmm, now_in_report_timezone
from telegram_bot import TelegramBot, tg_escape


def _telegram_local_clock_html() -> str:
    return f"🕐 <code>{tg_escape(format_report_local_hhmm())}</code>\n"


def _trade_headline_with_forecast_html(
    client: PolymarketClient,
    market: Dict[str, Any],
    headline_html: str,
) -> str:
    snap = ""
    try:
        from forecast.trade_snapshot import temp_market_trade_context_html

        snap = temp_market_trade_context_html(client, market) or ""
    except Exception:
        pass
    if not snap:
        try:
            from notifications.forecast_cache_fmt import (
                portfolio_position_forecast_html,
            )

            snap = portfolio_position_forecast_html(
                str(market.get("id") or ""),
                str(market.get("question") or market.get("title") or ""),
                None,
            ) or ""
        except Exception:
            pass
    if snap:
        return f"{headline_html}\n{snap}"
    return headline_html


def _trade_csv_base(title: str) -> Dict[str, Any]:
    ts = now_in_report_timezone()
    return {
        "timestamp": ts.isoformat(),
        "local_hhmm": format_report_local_hhmm(ts),
        "market_title": str(title)[:220],
    }


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


def peer_market_ids_excluding_self(
    client: PolymarketClient,
    market: Dict[str, Any],
    event_cache: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    eids = gamma_event_ids_for_market(market)
    if not eids:
        return []
    eid = eids[0]
    if eid not in event_cache:
        event_cache[eid] = client.get_markets_for_event_id(eid)
    mid = str(market.get("id") or "").strip()
    out: List[str] = []
    for m in event_cache.get(eid) or []:
        sm = str(m.get("id") or "").strip()
        if sm and sm != mid:
            out.append(sm)
    return out


def place_buy(
    client: PolymarketClient,
    market: Dict[str, Any],
    state: Dict[str, Any],
    telegram: TelegramBot,
    probability: float,
    settings: RuntimeSettings,
    event_cache: Dict[str, List[Dict[str, Any]]],
) -> None:
    market_id = str(market.get("id") or "")
    if not market_id:
        return
    if market_id in settings.blacklist_market_ids:
        return
    if not churn_allows_buy(state, market_id):
        return

    title = (
        market.get("question")
        or market.get("title")
        or market.get("id", "unknown-market")
    )
    # --- verify real CLOB price before committing ---
    clob_price = client.get_clob_yes_price(market)
    effective_buy_max = momentum_effective_buy_max(settings.buy_max, settings)
    if clob_price > 0 and clob_price > effective_buy_max + 1e-9:
        print(
            term_wrap(
                TERM_DIM,
                f"[clob price] skip buy — CLOB yes={clob_price:.4f} > buy_max={effective_buy_max:.4f}"
                f"  (gamma said {probability:.4f})\n  {title}",
            )
        )
        return
    if clob_price > 0 and clob_price < settings.buy_min - 1e-9:
        print(
            term_wrap(
                TERM_DIM,
                f"[clob price] skip buy — CLOB yes={clob_price:.4f} < buy_min={settings.buy_min:.4f}"
                f"  (gamma said {probability:.4f})\n  {title}",
            )
        )
        return
    # use CLOB price as the real probability for sizing and state
    if clob_price > 0:
        probability = clob_price

    ok, runner, _ = passes_competition_lead_gap(
        client, market, probability, settings, event_cache
    )
    if not ok:
        print(
            term_wrap(
                TERM_DIM,
                f"[competition] skip buy — candidate {probability:.4f} vs runner_up {runner:.4f} "
                f"(min_lead={settings.min_lead_over_runner_up:.4f})\n  {title}",
            )
        )
        return

    forecast_usd_factor = 1.0
    parsed_fc = parse_highest_temp_title(str(title))
    if parsed_fc:
        ow_key = ""
        if getattr(settings, "enable_openweather_forecast", False):
            ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip()
        _om, _ow, cons = get_forecast_max_for_city_day(
            parsed_fc.city_key,
            parsed_fc.event_date,
            parsed_fc.tz_name,
            openweather_api_key=ow_key,
        )
        if cons is not None:
            margin = float(getattr(settings, "forecast_contradict_margin_c", 2.5))
            if getattr(
                settings, "forecast_gate_buy", False
            ) and forecast_contradicts_strongly(cons, parsed_fc, margin):
                print(
                    term_wrap(
                        TERM_DIM,
                        f"[forecast] skip buy — model contradicts this bracket "
                        f"(max≈{cons:.1f}°C)\n  {title}",
                    )
                )
                return
            strong_x = forecast_contradicts_strongly(cons, parsed_fc, margin)
            if getattr(settings, "forecast_reduce_usd_if_weak", False) and (
                not forecast_supports_yes(cons, parsed_fc) and not strong_x
            ):
                fac = float(getattr(settings, "forecast_weak_size_factor", 0.45))
                forecast_usd_factor = min(1.0, max(0.1, fac))

    balance_before = client.get_portfolio_balance(force_allowance_refresh=True)
    cash = float(balance_before.get("cash") or 0)
    usd = compute_buy_usd_amount(cash, probability, settings) * forecast_usd_factor
    if usd <= 0:
        frac, cap_hard, planned, reserve_usd, tradable = planned_buy_cap_lines(
            cash, settings
        )
        print(
            term_wrap(
                TERM_DIM,
                f"[sizing] skip buy — cash=${cash:.2f} reserve=${reserve_usd:.2f} "
                f"tradable=${tradable:.2f} frac={frac:.2f} "
                f"planned=${planned:.2f} min_order=${settings.min_order_notional_usd:.2f}\n"
                f"  {title}",
            )
        )
        return
    try:
        order = client.place_market_buy_yes(market, usd)
    except PolyApiException as err:
        print(term_wrap(TERM_RED, f"BUY FAILED: {err}\n  {title}"))
        try:
            if telegram.is_configured():
                telegram.send_html_chunks(
                    f"{_telegram_local_clock_html()}"
                    f"🔴 <b>BUY FAILED</b>\n{tg_escape(title)}\n<pre>{tg_escape(err)}</pre>"
                )
        except requests.RequestException:
            pass
        return
    except Exception as err:
        print(term_wrap(TERM_RED, f"BUY FAILED: {err!r}\n  {title}"))
        try:
            if telegram.is_configured():
                telegram.send_html_chunks(
                    f"{_telegram_local_clock_html()}"
                    f"🔴 <b>BUY FAILED</b>\n{tg_escape(title)}\n<pre>{tg_escape(repr(err))}</pre>"
                )
        except requests.RequestException:
            pass
        return
    shares = round(usd / probability, 6) if probability else 0.0
    active_trades = state.setdefault("active_trades", {})
    active_trades[market_id] = {
        "market_id": market_id,
        "position_title": str(title).strip(),
        "shares": shares,
        "last_action": "buy",
        "entry_price": probability,
        "last_price": probability,
        "order_ref": order_ref_from_response(order),
    }
    frac, cap_hard, cap_usd, reserve_usd, tradable = planned_buy_cap_lines(
        cash, settings
    )
    headline = (
        "TRADE: BUY YES\n"
        f"{title}\n"
        f"cash=${cash:.2f}  reserve=${reserve_usd:.2f}  tradable=${tradable:.2f}\n"
        f"notional_usd={usd:.2f}  est_shares~={shares:.6f}  yes_price~={probability:.4f}\n"
        f"size_rule=min({frac * 100:.0f}%×tradable=${tradable * frac:.2f}, "
        f"cap=${cap_hard:.0f}) → planned=${cap_usd:.2f}\n"
        f"order_ref={order_ref_from_response(order) or order}"
    )
    oref = order_ref_from_response(order) or str(order)
    headline_html = (
        f"🟢 <b>BUY YES</b> <i>(bot)</i>\n"
        f"{tg_escape(title)}\n"
        f"💰 cash <code>${cash:.2f}</code>  ·  reserve <code>${reserve_usd:.2f}</code>"
        f"  ·  tradable <code>${tradable:.2f}</code>\n"
        f"💵 <code>${usd:.2f}</code>  ·  shares ~<code>{shares:.4f}</code>"
        f"  ·  yes ~<code>{probability:.4f}</code>\n"
        f"{format_buy_max_risk_line_html(usd)}\n"
        f"📏 min({frac * 100:.0f}%×tradable, ${cap_hard:.0f} cap) → "
        f"<code>${cap_usd:.2f}</code>\n"
        f"📝 <code>{tg_escape(oref)}</code>"
    )
    log_trade_buy_terminal(
        str(title),
        usd,
        shares,
        probability,
        order_ref_from_response(order),
    )
    append_ledger_row(
        {
            "ts_iso": now_in_report_timezone().isoformat(),
            "side": "buy",
            "market_id": market_id,
            "usd": round(usd, 4),
            "yes_price": round(probability, 6),
            "shares_est": shares,
        }
    )
    balance_after = client.get_portfolio_balance(force_allowance_refresh=False)
    cash_after = float(balance_after.get("cash") or 0)
    mtm_after = float(balance_after.get("positions_market_value") or 0)
    append_trade_csv_row(
        {
            **_trade_csv_base(str(title)),
            "action": "BUY",
            "market_id": market_id,
            "city": _extract_city_from_title(str(title)),
            "price": round(probability, 4),
            "shares": round(shares, 4),
            "usd": round(usd, 2),
            "reason": "",
            "entry_price": round(probability, 4),
            "pnl_usd": 0.0,
            "cash_after": round(cash_after, 2),
            "positions_mtm": round(mtm_after, 2),
            "total_value": round(cash_after + mtm_after, 2),
        }
    )
    try:
        send_portfolio_telegram(
            telegram,
            client,
            headline,
            headline_html=_trade_headline_with_forecast_html(
                client, market, headline_html
            ),
        )
    except Exception as err:
        print(term_wrap(TERM_RED, f"buy ok but telegram failed: {err!r}"))


def close_position(
    client: PolymarketClient,
    market: Dict[str, Any],
    state: Dict[str, Any],
    telegram: TelegramBot,
    probability: float,
    reason: str,
    settings: RuntimeSettings,
    *,
    state_trade_key: Optional[str] = None,
) -> None:
    market_id = str(market.get("id") or "")
    active_trades = state.get("active_trades", {})
    key = (state_trade_key or market_id).strip()
    trade = active_trades.get(key)
    if not market_id or not trade:
        return
    if trade.get("pending_limit_sell_order_id"):
        return

    cool_until = float(trade.get("_skip_sell_below_min_until") or 0)
    if cool_until and time.time() < cool_until:
        if reason not in SELL_BYPASS_MIN_COOLDOWN_REASONS:
            return
        if not trade.get("_bypass_min_cooldown_logged"):
            trade["_bypass_min_cooldown_logged"] = True
            print(
                term_wrap(
                    TERM_DIM,
                    f"[sell] bypass below-min cooldown ({reason})\n  "
                    f"{market.get('question') or market.get('title') or key}",
                )
            )

    if not trade_row_matches_gamma_market(trade, market):
        return

    shares_state = float(trade.get("shares", DEFAULT_ORDER_SIZE) or DEFAULT_ORDER_SIZE)
    title = (
        market.get("question")
        or market.get("title")
        or market.get("id", "unknown-market")
    )
    entry = float(trade.get("entry_price", trade.get("last_price", 0)) or 0)
    exchange_yes = client.get_yes_shares_on_exchange_for_market_id(key)
    if exchange_yes < DUST_SHARES_EPS:
        active_trades.pop(key, None)
        msg = (
            "SELL N/A: no YES size on exchange (flat / resolved / not in data-api)\n"
            f"tracked_state_shares={shares_state:.6f}\n"
            f"{title}"
        )
        print(term_wrap(TERM_YELLOW, msg))
        try:
            if telegram.is_configured():
                telegram.send_html_chunks(
                    f"{_telegram_local_clock_html()}"
                    f"🟡 <b>SELL N/A</b> (no YES on exchange)\n"
                    f"{tg_escape(title)}\n"
                    f"<code>state_shares={shares_state:.6f}</code>"
                )
        except requests.RequestException:
            pass
        return

    exchange_yes_pre = exchange_yes
    exchange_yes = client.topup_yes_if_needed_for_min_sell(market, exchange_yes, key)
    if exchange_yes > exchange_yes_pre + 1e-6:
        trade.pop("_skip_sell_below_min_until", None)
        trade.pop("_below_min_order_size", None)
        trade.pop("_bypass_min_cooldown_logged", None)
        print(
            term_wrap(
                TERM_DIM,
                f"[sell] topped up YES for min size: {exchange_yes_pre:.4f} -> {exchange_yes:.4f}\n"
                f"  {title}",
            )
        )
        sell_shares = exchange_yes
    else:
        sell_shares = min(shares_state, exchange_yes)
    if sell_shares + 1e-12 < shares_state and exchange_yes <= exchange_yes_pre + 1e-6:
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
            if telegram.is_configured():
                telegram.send_html_chunks(
                    f"{_telegram_local_clock_html()}"
                    f"🔴 <b>SELL FAILED</b> <code>{tg_escape(reason)}</code>\n"
                    f"{tg_escape(title)}\n"
                    f"shares <code>{sell_shares:.6f}</code>  ·  exch ~<code>{exchange_yes:.6f}</code>"
                    f"  ·  mark <code>{probability:.4f}</code>\n"
                    f"<pre>{tg_escape(detail)}</pre>"
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
            if telegram.is_configured():
                telegram.send_html_chunks(
                    f"{_telegram_local_clock_html()}"
                    f"🔴 <b>SELL FAILED</b> <code>{tg_escape(reason)}</code>\n"
                    f"{tg_escape(title)}\n"
                    f"shares <code>{sell_shares:.6f}</code>  ·  exch ~<code>{exchange_yes:.6f}</code>"
                    f"  ·  mark <code>{probability:.4f}</code>\n"
                    f"<pre>{tg_escape(repr(err))}</pre>"
                )
        except requests.RequestException:
            pass
        return

    trade.pop("_sell_err_fingerprint", None)

    if result.get("sell_execution") == SELL_EXECUTION_SKIPPED:
        body = result.get("sell_attempt_summary") or ""
        if result.get("skip_reason") == "below_min_order_size":
            min_sz = float(result.get("min_order_size") or 5.0)
            trade["_skip_sell_below_min_until"] = (
                time.time() + SELL_BELOW_MIN_COOLDOWN_SEC
            )
            trade["_below_min_order_size"] = min_sz
            msg = (
                f"SELL SKIPPED: {result.get('skip_reason')}\n"
                f"{title}\n"
                f"attempted_shares={result.get('attempted_shares')}  "
                f"clob_min_order_size={result.get('min_order_size')}\n"
                f"(holding in state — below CLOB min; retry later or redeem/merge)\n"
                f"---\n{body}"
            )
        else:
            active_trades.pop(key, None)
            msg = (
                f"SELL SKIPPED: {result.get('skip_reason')}\n"
                f"{title}\n"
                f"attempted_shares={result.get('attempted_shares')}  "
                f"clob_min_order_size={result.get('min_order_size')}\n"
                f"(removed from bot state)\n"
                f"---\n{body}"
            )
        print(term_wrap(TERM_YELLOW, msg))
        try:
            if telegram.is_configured():
                emoji = (
                    "⏸️" if result.get("skip_reason") == "below_min_order_size" else "⚠️"
                )
                telegram.send_html_chunks(
                    f"{_telegram_local_clock_html()}"
                    f"{emoji} <b>SELL SKIPPED</b> <code>{tg_escape(str(result.get('skip_reason')))}</code>\n"
                    f"{tg_escape(title)}\n<pre>{tg_escape(msg)}</pre>"
                )
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
                if telegram.is_configured():
                    telegram.send_html_chunks(
                        f"{_telegram_local_clock_html()}"
                        f"⚠️ <b>LIMIT SELL</b> — no order id in response\n{tg_escape(title)}"
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
        est_line = format_est_pnl_line_html(sell_shares, entry, limit_px)
        headline_html = (
            f"🟠 <b>LIMIT SELL</b> <code>{tg_escape(reason)}</code>\n"
            f"{tg_escape(title)}\n"
            f"📦 <code>{sell_shares:.6f}</code>  ·  limit <code>{limit_px:.4f}</code>"
            f"  ·  mark <code>{probability:.4f}</code>\n"
            f"{est_line}\n"
            f"📝 <code>{tg_escape(oid)}</code>"
        )
        log_limit_sell_posted_terminal(str(title), reason, sell_shares, limit_px, oid)
        balance_after = client.get_portfolio_balance(force_allowance_refresh=False)
        cash_after = float(balance_after.get("cash") or 0)
        mtm_after = float(balance_after.get("positions_market_value") or 0)
        est_lim = (limit_px - entry) * sell_shares
        append_trade_csv_row(
            {
                **_trade_csv_base(str(title)),
                "action": "LIMIT_SELL",
                "market_id": market_id,
                "city": _extract_city_from_title(str(title)),
                "price": round(limit_px, 4),
                "shares": round(sell_shares, 4),
                "usd": round(sell_shares * limit_px, 2),
                "reason": reason,
                "entry_price": round(entry, 4),
                "pnl_usd": round(est_lim, 2),
                "cash_after": round(cash_after, 2),
                "positions_mtm": round(mtm_after, 2),
                "total_value": round(cash_after + mtm_after, 2),
            }
        )
        try:
            send_portfolio_telegram(
                telegram,
                client,
                headline,
                headline_html=_trade_headline_with_forecast_html(
                    client, market, headline_html
                ),
            )
        except Exception as err:
            print(
                term_wrap(TERM_RED, f"limit sell posted but telegram failed: {err!r}")
            )
        return

    active_trades.pop(key, None)
    summary = result.get("sell_attempt_summary") or ""
    headline = (
        f"TRADE: SELL ({reason})\n"
        f"{title}\n"
        f"shares={sell_shares:.6f}  entry~={entry:.4f}  mark~={probability:.4f}\n"
        f"---\n{summary}"
    )
    est_pnl = (probability - entry) * sell_shares
    headline_html = (
        f"🔴 <b>SELL</b> <code>{tg_escape(reason)}</code> <i>(bot)</i>\n"
        f"{tg_escape(title)}\n"
        f"📦 <code>{sell_shares:.6f}</code>  ·  entry <code>{entry:.4f}</code>"
        f"  ·  mark <code>{probability:.4f}</code>\n"
        f"{format_est_pnl_line_html(sell_shares, entry, probability)}"
    )
    log_trade_sell_terminal(
        str(title), reason, sell_shares, entry, probability, failed=False
    )
    append_ledger_row(
        {
            "ts_iso": now_in_report_timezone().isoformat(),
            "side": "sell",
            "market_id": market_id,
            "reason": reason,
            "shares": sell_shares,
            "entry": entry,
            "mark": probability,
            "est_pnl_usd": round(est_pnl, 4),
        }
    )
    balance_after = client.get_portfolio_balance(force_allowance_refresh=False)
    cash_after = float(balance_after.get("cash") or 0)
    mtm_after = float(balance_after.get("positions_market_value") or 0)
    append_trade_csv_row(
        {
            **_trade_csv_base(str(title)),
            "action": "SELL",
            "market_id": market_id,
            "city": _extract_city_from_title(str(title)),
            "price": round(probability, 4),
            "shares": round(sell_shares, 4),
            "usd": round(sell_shares * probability, 2),
            "reason": reason,
            "entry_price": round(entry, 4),
            "pnl_usd": round(est_pnl, 2),
            "cash_after": round(cash_after, 2),
            "positions_mtm": round(mtm_after, 2),
            "total_value": round(cash_after + mtm_after, 2),
        }
    )
    if reason == "stop-loss":
        churn_on_stop_loss_exit(
            state,
            market_id,
            settings.churn_max_stop_cycles,
            settings.churn_cooldown_sec,
        )
    elif reason == "take-profit":
        churn_on_take_profit(state, market_id)

    try:
        send_portfolio_telegram(
            telegram,
            client,
            headline,
            headline_html=_trade_headline_with_forecast_html(
                client, market, headline_html
            ),
        )
    except Exception as err:
        print(term_wrap(TERM_RED, f"sell ok but telegram failed: {err!r}"))


def claim_position(
    client: PolymarketClient,
    market: Dict[str, Any],
    state: Dict[str, Any],
    telegram: TelegramBot,
    *,
    state_trade_key: Optional[str] = None,
) -> None:
    market_id = str(market.get("id") or "")
    if not market_id:
        return
    key = (state_trade_key or market_id).strip()
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
            if telegram.is_configured():
                telegram.send_html_chunks(
                    f"{_telegram_local_clock_html()}"
                    f"🔴 <b>CLAIM FAILED</b>\n{tg_escape(title)}\n<pre>{tg_escape(repr(err))}</pre>"
                )
        except requests.RequestException:
            pass
        return
    state.get("active_trades", {}).pop(key, None)
    headline = f"TRADE: CLAIM\n{title}"
    headline_html = _trade_headline_with_forecast_html(
        client,
        market,
        f"🟡 <b>CLAIM</b> <i>(bot)</i>\n{tg_escape(title)}",
    )
    log_trade_claim_terminal(str(title))
    balance_after = client.get_portfolio_balance(force_allowance_refresh=False)
    cash_after = float(balance_after.get("cash") or 0)
    mtm_after = float(balance_after.get("positions_market_value") or 0)
    append_trade_csv_row(
        {
            **_trade_csv_base(str(title)),
            "action": "CLAIM",
            "market_id": market_id,
            "city": _extract_city_from_title(str(title)),
            "price": 0.0,
            "shares": 0.0,
            "usd": 0.0,
            "reason": "claim",
            "entry_price": 0.0,
            "pnl_usd": 0.0,
            "cash_after": round(cash_after, 2),
            "positions_mtm": round(mtm_after, 2),
            "total_value": round(cash_after + mtm_after, 2),
        }
    )
    try:
        send_portfolio_telegram(telegram, client, headline, headline_html=headline_html)
    except Exception as err:
        print(term_wrap(TERM_RED, f"claim ok but telegram failed: {err!r}"))


def process_single_market(
    client: PolymarketClient,
    telegram: TelegramBot,
    state: Dict[str, Any],
    market: Dict[str, Any],
    settings: RuntimeSettings,
    event_cache: Dict[str, List[Dict[str, Any]]],
    *,
    allow_new_buys: bool,
) -> None:
    market_id = str(market.get("id") or "")
    if not market_id:
        return

    active_trades = state.setdefault("active_trades", {})
    gamma_probability = parse_market_probability(market)
    status = market_status(market)
    trade_key, trade_row = active_trade_key_and_row(active_trades, market)
    has_position = trade_row is not None
    sk = trade_key if trade_key else None

    if has_position and not trade_row_matches_gamma_market(trade_row, market):
        warn_gamma_trade_mismatch_once(trade_row, market, telegram)
        return

    if has_position and status in STATUS_CLOSED:
        claim_position(client, market, state, telegram, state_trade_key=sk)
        return

    if (
        settings.enable_momentum
        and has_position
        and trade_row
        and market_can_post_clob_orders(market)
    ):
        peers = peer_market_ids_excluding_self(client, market, event_cache)
        if peers:
            drop = max_peer_yes_drop_ratio(peers, settings.momentum_window_min)
            if drop + 1e-9 >= settings.momentum_peer_drop:
                close_position(
                    client,
                    market,
                    state,
                    telegram,
                    gamma_probability,
                    "momentum-peer-drop",
                    settings,
                    state_trade_key=sk,
                )
                return

    if (
        getattr(settings, "enable_flow_peer_exit", False)
        and has_position
        and trade_row
        and market_can_post_clob_orders(market)
    ):
        feids = gamma_event_ids_for_market(market)
        if feids and flow_peer_recommends_exit(
            market_id,
            feids[0],
            settings,
            now_ts=time.time(),
        ):
            close_position(
                client,
                market,
                state,
                telegram,
                gamma_probability,
                "flow-peer-surge",
                settings,
                state_trade_key=sk,
            )
            return

    if (
        getattr(settings, "research_exit_on_model_flip", False)
        and has_position
        and trade_row
        and market_can_post_clob_orders(market)
    ):
        t_exit = str(market.get("question") or market.get("title") or "")
        p_exit = parse_highest_temp_title(t_exit)
        if p_exit:
            ow_key_exit = ""
            if getattr(settings, "enable_openweather_forecast", False):
                ow_key_exit = os.getenv("OPENWEATHER_API_KEY", "").strip()
            _om_e, _ow_e, cons_e = get_forecast_max_for_city_day(
                p_exit.city_key,
                p_exit.event_date,
                p_exit.tz_name,
                openweather_api_key=ow_key_exit,
            )
            if cons_e is not None:
                margin_e = float(getattr(settings, "forecast_contradict_margin_c", 2.5))
                if forecast_contradicts_strongly(cons_e, p_exit, margin_e):
                    close_position(
                        client,
                        market,
                        state,
                        telegram,
                        gamma_probability,
                        "research-model-flip",
                        settings,
                        state_trade_key=sk,
                    )
                    return

    prob_stop = stop_loss_reference_if_triggered(market, trade_row, settings.stop_loss)
    if prob_stop is not None:
        close_position(
            client,
            market,
            state,
            telegram,
            prob_stop,
            "stop-loss",
            settings,
            state_trade_key=sk,
        )
        return

    prob_take_profit = take_profit_decision_probability(market, trade_row)
    tp_bar = settings.take_profit - TAKE_PROFIT_COMPARE_SLACK
    if has_position and (prob_take_profit + 1e-9 >= tp_bar):
        close_position(
            client,
            market,
            state,
            telegram,
            prob_take_profit,
            "take-profit",
            settings,
            state_trade_key=sk,
        )
        return

    if has_position and not market_can_post_clob_orders(market):
        tr = active_trades[trade_key]
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
                if telegram.is_configured():
                    telegram.send_html_chunks(
                        f"{_telegram_local_clock_html()}"
                        f"⏸️ <b>EXIT PAUSED</b> (CLOB off)\n"
                        f"{tg_escape(t)}\n<pre>{tg_escape(warn)}</pre>"
                    )
            except requests.RequestException:
                pass
            tr["_warned_clob_disabled"] = True
        return

    if has_position:
        active_trades[trade_key].pop("_warned_clob_disabled", None)

    buy_min = settings.buy_min
    buy_max = momentum_effective_buy_max(settings.buy_max, settings)
    mom_ok = momentum_entry_allowed(market_id, gamma_probability, settings)

    if allow_new_buys and (not has_position) and market_can_post_clob_orders(market):
        # --- time gate: no buys before BUY_EARLIEST_HOUR in city's timezone ---
        from strategy.city_tz import city_local_hour

        _title = str(market.get("question") or market.get("title") or "")
        _city_hour = city_local_hour(_title)
        if _city_hour is not None and _city_hour < BUY_EARLIEST_HOUR:
            return
        # --- position count gate ---
        open_count = len(active_trades)
        if open_count >= MAX_CONCURRENT_POSITIONS:
            return
        in_band = buy_min <= gamma_probability <= buy_max
        if not in_band and mom_ok and gamma_probability <= settings.momentum_max_entry:
            in_band = buy_min <= gamma_probability <= settings.momentum_max_entry
        if in_band:
            place_buy(
                client,
                market,
                state,
                telegram,
                gamma_probability,
                settings,
                event_cache,
            )
