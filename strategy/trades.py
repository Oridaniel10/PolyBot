import hashlib
import os
import time
from typing import Any, Dict, List, Optional

import requests
from py_clob_client_v2.exceptions import PolyApiException

from config.constants import (
    BUY_BLOCK_EVENT_DATE_AFTER_ISRAEL_TODAY,
    CLOB_SELL_TOPUP_MAX_ROUNDS,
    DEFAULT_ORDER_SIZE,
    DUST_SHARES_EPS,
    FORECAST_CONTRADICT_MARGIN_C,
    FORECAST_EXACT_BUCKET_SUPPORT_SLACK_C,
    MAX_CONCURRENT_POSITIONS,
    DOUBLE_MOMENTUM_ENTRY_RISE,
    DOUBLE_MOMENTUM_MAX_PRICE,
    DOUBLE_MOMENTUM_MIN_PRICE,
    MOMENTUM_MAX_ENTRY,
    MOMENTUM_MIN_PRICE,
    SELL_BELOW_MIN_COOLDOWN_SEC,
    SELL_BELOW_MIN_TELEGRAM_COOLDOWN_SEC,
    SELL_BYPASS_MIN_COOLDOWN_REASONS,
    STATUS_CLOSED,
    TAKE_PROFIT_COMPARE_SLACK,
    TERM_DIM,
    TERM_RED,
    TERM_YELLOW,
    TRADE_BUY_LOCK_TTL_SEC,
    TRADE_RECENT_SELL_TTL_SEC,
    TRADE_SELL_LOCK_TTL_SEC,
)
from config.settings import RuntimeSettings, get_effective_settings
from notifications.portfolio import send_portfolio_telegram
from notifications.research_trade_fmt import (
    format_decision_engine_html,
    format_decision_skip_html,
    format_research_context_html,
    decision_skip_telegram_allowed,
)
from notifications.telegram_fmt import (
    format_buy_exit_plan_html,
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
from strategy.city_tz import _extract_city_from_title, city_local_time_str
from strategy.churn import (
    churn_on_stop_loss_exit,
    churn_on_take_profit,
    churn_allows_buy,
    churn_on_event_loss,
)
from strategy.gates import market_can_post_clob_orders, market_status
from strategy.market_match import (
    active_trade_key_and_row,
    trade_row_matches_gamma_market,
    warn_gamma_trade_mismatch_once,
)
from strategy.decision_core import (
    TradeDecision,
    check_exits,
    detect_momentum_switch,
    evaluate_entry,
    momentum_window_sec,
    stop_loss_bar_for_entry_type,
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
from strategy.momentum_engine import momentum_entry_signal
from strategy.research_signal import edge_size_multiplier
from strategy.sizing import compute_buy_usd_amount, planned_buy_cap_lines
from strategy.time_filter import entry_time_allowed
from strategy.time_utils import format_report_local_hhmm, now_in_report_timezone
from telegram_bot import TelegramBot, tg_escape


def _telegram_local_clock_html() -> str:
    return f"🕐 <code>{tg_escape(format_report_local_hhmm())}</code>\n"


def _city_local_clock_html(title: str) -> str:
    lt = city_local_time_str(str(title))
    if not lt:
        return ""
    return f"📍 <b>city local</b> <code>{tg_escape(lt)}</code>\n"


def _status_portfolio_headline_html(inner_html: str, action_label: str) -> str:
    return f"<b>📊 STATUS</b> · <i>{tg_escape(action_label)}</i>\n{inner_html}"


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

            snap = (
                portfolio_position_forecast_html(
                    str(market.get("id") or ""),
                    str(market.get("question") or market.get("title") or ""),
                    None,
                )
                or ""
            )
        except Exception:
            pass
    if snap:
        return f"{headline_html}\n{snap}"
    return headline_html


def _trade_csv_base(title: str) -> Dict[str, Any]:
    ts = now_in_report_timezone()
    st = str(title)
    return {
        "timestamp": ts.isoformat(),
        "local_hhmm": format_report_local_hhmm(ts),
        "city_local_hhmm": city_local_time_str(st),
        "entry_type": "",
        "decision_reason": "",
        "market_title": st[:220],
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


_TRADE_CSV_RESEARCH_PAD = {
    "consensus_c": "",
    "implied_yes": "",
    "edge": "",
    "edge_raw": "",
    "edge_soft_boost": "",
    "required_edge": "",
    "fee_drag": "",
}

_TRADE_CSV_PLAN_PAD = {
    "tp_exit_bar": "",
    "sl_mark_bar": "",
    "buy_est_tp_pnl_usd": "",
    "buy_est_sl_pnl_usd": "",
    "buy_est_yes_resolve_pnl_usd": "",
}


def market_event_id(market: Dict[str, Any]) -> str:
    eids = gamma_event_ids_for_market(market)
    if not eids:
        return ""
    return str(eids[0]).strip()


def prune_trade_locks(state: Dict[str, Any]) -> None:
    now = time.time()
    for root_key in ("trade_locks", "recent_sells"):
        root = state.get(root_key) or {}
        if not isinstance(root, dict):
            state[root_key] = {}
            continue
        for bucket_key, bucket in list(root.items()):
            if not isinstance(bucket, dict):
                root.pop(bucket_key, None)
                continue
            for key, until in list(bucket.items()):
                if now >= float(until or 0.0):
                    bucket.pop(key, None)
            if not bucket:
                root.pop(bucket_key, None)


def trade_lock_active(state: Dict[str, Any], bucket: str, key: str) -> bool:
    if not key:
        return False
    prune_trade_locks(state)
    locks = state.setdefault("trade_locks", {})
    group = locks.get(bucket) if isinstance(locks, dict) else {}
    if not isinstance(group, dict):
        return False
    return time.time() < float(group.get(key) or 0.0)


def set_trade_lock(
    state: Dict[str, Any], bucket: str, key: str, ttl_sec: float
) -> None:
    if not key or ttl_sec <= 0:
        return
    locks = state.setdefault("trade_locks", {})
    if not isinstance(locks, dict):
        locks = {}
        state["trade_locks"] = locks
    group = locks.setdefault(bucket, {})
    if not isinstance(group, dict):
        group = {}
        locks[bucket] = group
    group[key] = time.time() + float(ttl_sec)


def clear_trade_lock(state: Dict[str, Any], bucket: str, key: str) -> None:
    locks = state.get("trade_locks") or {}
    if not isinstance(locks, dict):
        return
    group = locks.get(bucket)
    if isinstance(group, dict):
        group.pop(key, None)


def mark_recent_sell(state: Dict[str, Any], market_id: str) -> None:
    if not market_id:
        return
    recent = state.setdefault("recent_sells", {})
    if not isinstance(recent, dict):
        recent = {}
        state["recent_sells"] = recent
    bucket = recent.setdefault("market", {})
    if not isinstance(bucket, dict):
        bucket = {}
        recent["market"] = bucket
    bucket[market_id] = time.time() + float(TRADE_RECENT_SELL_TTL_SEC)


def market_recently_sold(state: Dict[str, Any], market_id: str) -> bool:
    if not market_id:
        return False
    prune_trade_locks(state)
    recent = state.get("recent_sells") or {}
    bucket = recent.get("market") if isinstance(recent, dict) else {}
    if not isinstance(bucket, dict):
        return False
    return time.time() < float(bucket.get(market_id) or 0.0)


def place_buy(
    client: PolymarketClient,
    market: Dict[str, Any],
    state: Dict[str, Any],
    telegram: TelegramBot,
    probability: float,
    settings: RuntimeSettings,
    event_cache: Dict[str, List[Dict[str, Any]]],
    trade_decision: Optional[TradeDecision] = None,
) -> None:
    market_id = str(market.get("id") or "")
    if not market_id:
        return
    event_id = market_event_id(market)
    active_trades = state.setdefault("active_trades", {})
    if active_trade_key_and_row(active_trades, market)[1] is not None:
        return
    if trade_lock_active(state, "buy_market", market_id):
        return
    if event_id and trade_lock_active(state, "buy_event", event_id):
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
    # verify real CLOB price before committing
    clob_price = client.get_clob_yes_price(market)
    mom_relax = bool(
        trade_decision is not None
        and getattr(trade_decision, "momentum_relaxed_gates", False)
    )
    mom_lo_pb = float(getattr(settings, "momentum_min_price", MOMENTUM_MIN_PRICE))
    mom_hi_pb = float(getattr(settings, "momentum_max_entry", MOMENTUM_MAX_ENTRY))
    entry_type = "normal"
    if trade_decision:
        entry_type = getattr(trade_decision, "entry_type", "normal") or "normal"
    if entry_type == "double_momentum":
        mom_lo_pb = float(
            getattr(settings, "double_momentum_min_price", DOUBLE_MOMENTUM_MIN_PRICE)
        )
        mom_hi_pb = float(
            getattr(settings, "double_momentum_max_price", DOUBLE_MOMENTUM_MAX_PRICE)
        )
    if not getattr(settings, "buy_disable_price_band", False):
        hi = mom_hi_pb if mom_relax else float(settings.buy_max)
        lo = mom_lo_pb if mom_relax else float(settings.buy_min)
        if clob_price > 0 and clob_price > hi + 1e-9:
            print(
                term_wrap(
                    TERM_DIM,
                    f"[clob price] skip buy — CLOB yes={clob_price:.4f} > band_hi={hi:.4f}\n  {title}",
                )
            )
            return
        if clob_price > 0 and clob_price < lo - 1e-9:
            print(
                term_wrap(
                    TERM_DIM,
                    f"[clob price] skip buy — CLOB yes={clob_price:.4f} < band_lo={lo:.4f}\n  {title}",
                )
            )
            return
    if clob_price > 0:
        probability = clob_price

    # decision engine already validated — use its result
    research_decision = trade_decision.research if trade_decision else None
    forecast_usd_factor = 1.0
    if research_decision is not None:
        exm = edge_size_multiplier(
            research_decision.edge,
            research_decision.required_edge,
            scale_enabled=bool(settings.research_edge_scale_size),
            slope=float(settings.research_edge_size_slope),
            cap_mult=float(settings.research_edge_size_cap_mult),
        )
        forecast_usd_factor *= exm

    parsed_fc = parse_highest_temp_title(str(title))
    if parsed_fc:
        ow_blend = bool(getattr(settings, "enable_openweather_forecast", False))
        ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip() if ow_blend else ""
        _om, _ow, cons = get_forecast_max_for_city_day(
            parsed_fc.city_key,
            parsed_fc.event_date,
            parsed_fc.tz_name,
            openweather_api_key=ow_key,
            use_openweather=ow_blend,
        )
        if cons is not None:
            margin = float(
                getattr(
                    settings,
                    "forecast_contradict_margin_c",
                    FORECAST_CONTRADICT_MARGIN_C,
                )
            )
            if (
                getattr(settings, "forecast_gate_buy", False)
                and not mom_relax
                and forecast_contradicts_strongly(cons, parsed_fc, margin)
            ):
                print(
                    term_wrap(
                        TERM_DIM,
                        f"[forecast] skip buy — model contradicts bracket (max≈{cons:.1f}°C)\n  {title}",
                    )
                )
                return
            if getattr(settings, "forecast_reduce_usd_if_weak", False):
                if not forecast_supports_yes(
                    cons,
                    parsed_fc,
                    exact_slack_c=float(
                        getattr(
                            settings,
                            "forecast_exact_bucket_support_slack_c",
                            FORECAST_EXACT_BUCKET_SUPPORT_SLACK_C,
                        )
                    ),
                ) and not forecast_contradicts_strongly(cons, parsed_fc, margin):
                    fac = float(getattr(settings, "forecast_weak_size_factor", 0.45))
                    forecast_usd_factor *= min(1.0, max(0.1, fac))

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
    set_trade_lock(state, "buy_market", market_id, TRADE_BUY_LOCK_TTL_SEC)
    if event_id:
        set_trade_lock(state, "buy_event", event_id, TRADE_BUY_LOCK_TTL_SEC)
    try:
        order = client.place_market_buy_yes(market, usd)
    except PolyApiException as err:
        clear_trade_lock(state, "buy_market", market_id)
        if event_id:
            clear_trade_lock(state, "buy_event", event_id)
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
        clear_trade_lock(state, "buy_market", market_id)
        if event_id:
            clear_trade_lock(state, "buy_event", event_id)
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
    tp_bar = max(
        0.0,
        min(1.0, float(settings.take_profit) - TAKE_PROFIT_COMPARE_SLACK),
    )
    # per-type SL: use entry_type from decision engine
    sl_bar = stop_loss_bar_for_entry_type(entry_type, settings)
    est_tp_pnl = (tp_bar - float(probability)) * float(shares)
    est_sl_pnl = (sl_bar - float(probability)) * float(shares)
    est_yes_pnl = (1.0 - float(probability)) * float(shares)
    active_trades = state.setdefault("active_trades", {})
    active_trades[market_id] = {
        "market_id": market_id,
        "position_title": str(title).strip(),
        "shares": shares,
        "last_action": "buy",
        "entry_price": probability,
        "last_price": probability,
        "order_ref": order_ref_from_response(order),
        "tp_exit_bar": tp_bar,
        "sl_mark_bar": sl_bar,
        "entry_time_utc": now_in_report_timezone().isoformat(),
        "entry_type": entry_type,
    }
    clear_trade_lock(state, "buy_market", market_id)
    if event_id:
        clear_trade_lock(state, "buy_event", event_id)
    frac, cap_hard, cap_usd, reserve_usd, tradable = planned_buy_cap_lines(
        cash, settings
    )
    headline = (
        "TRADE: BUY YES\n"
        f"{title}\n"
        f"cash=${cash:.2f}  reserve=${reserve_usd:.2f}  tradable=${tradable:.2f}\n"
        f"notional_usd={usd:.2f}  est_shares~={shares:.6f}  yes_price~={probability:.4f}\n"
        f"tp_exit_bar~={tp_bar:.4f}  sl_mark_bar~={sl_bar:.4f}  "
        f"est_pnl@tp~{est_tp_pnl:+.2f}  est_pnl@sl~{est_sl_pnl:+.2f}  "
        f"est_pnl_if_YES_$1~{est_yes_pnl:+.2f}\n"
        f"size_rule=min({frac * 100:.0f}%×tradable=${tradable * frac:.2f}, "
        f"cap=${cap_hard:.0f}) → planned=${cap_usd:.2f}\n"
        f"order_ref={order_ref_from_response(order) or order}"
    )
    oref = order_ref_from_response(order) or str(order)
    headline_html = _status_portfolio_headline_html(
        (
            f"{_city_local_clock_html(str(title))}"
            f"🟢 <b>BUY YES</b> <i>(bot)</i>\n"
            f"{tg_escape(title)}\n"
            f"💰 cash <code>${cash:.2f}</code>  ·  reserve <code>${reserve_usd:.2f}</code>"
            f"  ·  tradable <code>${tradable:.2f}</code>\n"
            f"💵 <code>${usd:.2f}</code>  ·  shares ~<code>{shares:.4f}</code>"
            f"  ·  yes ~<code>{probability:.4f}</code>\n"
            f"{format_buy_max_risk_line_html(usd)}\n"
            f"{format_buy_exit_plan_html(probability, shares, tp_bar, sl_bar)}\n"
            f"📏 min({frac * 100:.0f}%×tradable, ${cap_hard:.0f} cap) → "
            f"<code>${cap_usd:.2f}</code>\n"
            f"📝 <code>{tg_escape(oref)}</code>"
        ),
        "After BUY",
    )
    if research_decision is not None:
        headline_html += format_research_context_html(
            consensus_c=research_decision.consensus_c,
            implied_yes=research_decision.implied_yes,
            edge=research_decision.edge,
            required_edge=research_decision.required_edge,
            fee_drag=research_decision.fee_drag,
            clob_yes=probability,
            edge_raw=research_decision.edge_raw,
            edge_soft_boost=research_decision.edge_soft_boost,
        )
    if trade_decision is not None:
        headline_html += format_decision_engine_html(trade_decision)
    log_trade_buy_terminal(
        str(title),
        usd,
        shares,
        probability,
        order_ref_from_response(order),
        tp_bar=tp_bar,
        sl_bar=sl_bar,
    )
    led_buy: Dict[str, Any] = {
        "ts_iso": now_in_report_timezone().isoformat(),
        "side": "buy",
        "market_id": market_id,
        "usd": round(usd, 4),
        "yes_price": round(probability, 6),
        "shares_est": shares,
        "tp_exit_bar": round(tp_bar, 6),
        "sl_mark_bar": round(sl_bar, 6),
        "buy_est_tp_pnl_usd": round(est_tp_pnl, 4),
        "buy_est_sl_pnl_usd": round(est_sl_pnl, 4),
        "buy_est_yes_resolve_pnl_usd": round(est_yes_pnl, 4),
    }
    if research_decision is not None:
        led_buy["research_consensus_c"] = round(research_decision.consensus_c, 4)
        led_buy["research_implied_yes"] = round(research_decision.implied_yes, 6)
        led_buy["research_edge"] = round(research_decision.edge, 6)
        led_buy["research_edge_raw"] = round(research_decision.edge_raw, 6)
        led_buy["research_edge_soft_boost"] = round(
            research_decision.edge_soft_boost,
            6,
        )
        led_buy["research_required_edge"] = round(research_decision.required_edge, 6)
    if trade_decision is not None:
        led_buy["decision_sigma_c"] = round(trade_decision.sigma_used, 4)
        led_buy["decision_model_prob"] = round(trade_decision.model_prob, 6)
        led_buy["decision_market_prob"] = round(trade_decision.market_prob, 6)
        led_buy["decision_edge"] = round(trade_decision.edge, 6)
        led_buy["decision_reason"] = str(trade_decision.reason)[:200]
    else:
        led_buy["decision_reason"] = "bot_buy_without_decision_object"
    append_ledger_row(led_buy)
    balance_after = client.get_portfolio_balance(force_allowance_refresh=False)
    cash_after = float(balance_after.get("cash") or 0)
    mtm_after = float(balance_after.get("positions_market_value") or 0)
    csv_buy: Dict[str, Any] = {
        **_trade_csv_base(str(title)),
        **_TRADE_CSV_RESEARCH_PAD,
        "action": "BUY",
        "market_id": market_id,
        "city": _extract_city_from_title(str(title)),
        "price": round(probability, 4),
        "shares": round(shares, 4),
        "usd": round(usd, 2),
        "reason": (
            str(trade_decision.reason)[:160]
            if trade_decision is not None
            else "bot_buy_without_decision_object"
        ),
        "entry_type": str(entry_type),
        "decision_reason": (
            str(trade_decision.reason)[:200]
            if trade_decision is not None
            else "bot_buy_without_decision_object"
        ),
        "entry_price": round(probability, 4),
        "pnl_usd": 0.0,
        "cash_after": round(cash_after, 2),
        "positions_mtm": round(mtm_after, 2),
        "total_value": round(cash_after + mtm_after, 2),
        "tp_exit_bar": round(tp_bar, 4),
        "sl_mark_bar": round(sl_bar, 4),
        "buy_est_tp_pnl_usd": round(est_tp_pnl, 2),
        "buy_est_sl_pnl_usd": round(est_sl_pnl, 2),
        "buy_est_yes_resolve_pnl_usd": round(est_yes_pnl, 2),
    }
    if research_decision is not None:
        csv_buy["consensus_c"] = round(research_decision.consensus_c, 4)
        csv_buy["implied_yes"] = round(research_decision.implied_yes, 6)
        csv_buy["edge"] = round(research_decision.edge, 6)
        csv_buy["edge_raw"] = round(research_decision.edge_raw, 6)
        csv_buy["edge_soft_boost"] = round(research_decision.edge_soft_boost, 6)
        csv_buy["required_edge"] = round(research_decision.required_edge, 6)
        csv_buy["fee_drag"] = round(research_decision.fee_drag, 6)
    if trade_decision is not None:
        csv_buy["decision_sigma_c"] = round(trade_decision.sigma_used, 4)
        csv_buy["decision_model_prob"] = round(trade_decision.model_prob, 6)
    append_trade_csv_row(csv_buy)
    try:
        send_portfolio_telegram(
            telegram,
            client,
            headline,
            headline_html=_trade_headline_with_forecast_html(
                client,
                market,
                headline_html,
            ),
            state=state,
            blacklist_ids=settings.blacklist_market_ids,
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
    if market_recently_sold(state, market_id):
        return
    if trade_lock_active(state, "sell_market", market_id):
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
    set_trade_lock(state, "sell_market", market_id, TRADE_SELL_LOCK_TTL_SEC)

    shares_state = float(trade.get("shares", DEFAULT_ORDER_SIZE) or DEFAULT_ORDER_SIZE)
    title = (
        market.get("question")
        or market.get("title")
        or market.get("id", "unknown-market")
    )
    held_entry_type = str(trade.get("entry_type") or "").strip()
    entry = float(trade.get("entry_price", trade.get("last_price", 0)) or 0)
    exchange_yes = client.get_yes_shares_on_exchange_for_market_id(key)
    if exchange_yes < DUST_SHARES_EPS:
        active_trades.pop(key, None)
        clear_trade_lock(state, "sell_market", market_id)
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

    exchange_yes_pre_loop = float(exchange_yes)
    ey = exchange_yes_pre_loop
    max_rounds = max(1, int(CLOB_SELL_TOPUP_MAX_ROUNDS))
    for round_i in range(max_rounds):
        min_book = client.get_clob_min_order_size_yes(market)
        need = float(min_book) if min_book is not None else 5.0
        if ey + 1e-9 >= need:
            break
        prev_ey = ey
        ey = client.topup_yes_if_needed_for_min_sell(market, ey, key)
        if ey > prev_ey + 1e-6:
            trade.pop("_skip_sell_below_min_until", None)
            trade.pop("_below_min_order_size", None)
            trade.pop("_bypass_min_cooldown_logged", None)
            trade.pop("_below_min_tg_not_before", None)
            print(
                term_wrap(
                    TERM_DIM,
                    f"[sell] topup round {round_i + 1}: {prev_ey:.4f} -> {ey:.4f} (need≥{need:.2f})\n"
                    f"  {title}",
                )
            )
        if ey <= prev_ey + 1e-6:
            break
        if round_i + 1 < max_rounds:
            time.sleep(0.2)
    exchange_yes = ey
    exchange_yes_pre = exchange_yes_pre_loop
    if exchange_yes > exchange_yes_pre + 1e-6:
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
        clear_trade_lock(state, "sell_market", market_id)
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
        clear_trade_lock(state, "sell_market", market_id)
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
            mark_recent_sell(state, market_id)
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
                send_tg = True
                if result.get("skip_reason") == "below_min_order_size":
                    nb = float(trade.get("_below_min_tg_not_before") or 0.0)
                    if time.time() < nb:
                        send_tg = False
                    else:
                        trade["_below_min_tg_not_before"] = time.time() + float(
                            SELL_BELOW_MIN_TELEGRAM_COOLDOWN_SEC
                        )
                if send_tg:
                    telegram.send_html_chunks(
                        f"{_telegram_local_clock_html()}"
                        f"{emoji} <b>SELL SKIPPED</b> <code>{tg_escape(str(result.get('skip_reason')))}</code>\n"
                        f"{tg_escape(title)}\n<pre>{tg_escape(msg)}</pre>"
                    )
        except requests.RequestException:
            pass
        clear_trade_lock(state, "sell_market", market_id)
        return
    if result.get("sell_execution") == SELL_EXECUTION_LIMIT_GTC:
        limit_px = float(result.get("limit_price") or 0)
        oid = order_ref_from_response(result)
        if not oid:
            clear_trade_lock(state, "sell_market", market_id)
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
        headline_html = _status_portfolio_headline_html(
            (
                f"{_city_local_clock_html(str(title))}"
                f"🟠 <b>LIMIT SELL</b> <code>{tg_escape(reason)}</code>\n"
                f"{tg_escape(title)}\n"
                f"📦 <code>{sell_shares:.6f}</code>  ·  limit <code>{limit_px:.4f}</code>"
                f"  ·  mark <code>{probability:.4f}</code>\n"
                f"{est_line}\n"
                f"📝 <code>{tg_escape(oid)}</code>"
            ),
            "LIMIT SELL",
        )
        log_limit_sell_posted_terminal(str(title), reason, sell_shares, limit_px, oid)
        balance_after = client.get_portfolio_balance(force_allowance_refresh=False)
        cash_after = float(balance_after.get("cash") or 0)
        mtm_after = float(balance_after.get("positions_market_value") or 0)
        est_lim = (limit_px - entry) * sell_shares
        append_trade_csv_row(
            {
                **_trade_csv_base(str(title)),
                **_TRADE_CSV_RESEARCH_PAD,
                **_TRADE_CSV_PLAN_PAD,
                "action": "LIMIT_SELL",
                "market_id": market_id,
                "city": _extract_city_from_title(str(title)),
                "entry_type": held_entry_type,
                "decision_reason": "",
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
                state=state,
                blacklist_ids=settings.blacklist_market_ids,
            )
        except Exception as err:
            print(
                term_wrap(TERM_RED, f"limit sell posted but telegram failed: {err!r}")
            )
        clear_trade_lock(state, "sell_market", market_id)
        return

    active_trades.pop(key, None)
    mark_recent_sell(state, market_id)
    clear_trade_lock(state, "sell_market", market_id)
    summary = result.get("sell_attempt_summary") or ""
    headline = (
        f"TRADE: SELL ({reason})\n"
        f"{title}\n"
        f"shares={sell_shares:.6f}  entry~={entry:.4f}  mark~={probability:.4f}\n"
        f"---\n{summary}"
    )
    est_pnl = (probability - entry) * sell_shares
    headline_html = _status_portfolio_headline_html(
        (
            f"{_city_local_clock_html(str(title))}"
            f"🔴 <b>SELL</b> <code>{tg_escape(reason)}</code> <i>(bot)</i>\n"
            f"{tg_escape(title)}\n"
            f"📦 <code>{sell_shares:.6f}</code>  ·  entry <code>{entry:.4f}</code>"
            f"  ·  mark <code>{probability:.4f}</code>\n"
            f"{format_est_pnl_line_html(sell_shares, entry, probability)}"
        ),
        "After SELL",
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
            **_TRADE_CSV_RESEARCH_PAD,
            **_TRADE_CSV_PLAN_PAD,
            "action": "SELL",
            "market_id": market_id,
            "city": _extract_city_from_title(str(title)),
            "entry_type": held_entry_type,
            "decision_reason": "",
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
    if reason in (
        "stop-loss",
        "peer-yes-surge",
        "momentum-stop-loss",
        "competitor-surge",
        "momentum-competitor-dominant",
        "time-decay",
    ):
        churn_on_stop_loss_exit(
            state,
            market_id,
            settings.churn_max_stop_cycles,
            settings.churn_cooldown_sec,
        )
        # event-level churn: track losses across sibling markets in same event
        try:
            from polymarket_client import gamma_event_ids_for_market

            eids = gamma_event_ids_for_market(market)
            if eids:
                churn_on_event_loss(
                    state,
                    str(eids[0]).strip(),
                    settings.churn_event_max_losses,
                    settings.churn_event_cooldown_sec,
                )
        except Exception:
            pass
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
            state=state,
            blacklist_ids=settings.blacklist_market_ids,
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
    active_trades = state.get("active_trades") or {}
    prev_row = active_trades.get(key) if isinstance(active_trades, dict) else None
    held_entry_type = ""
    if isinstance(prev_row, dict):
        held_entry_type = str(prev_row.get("entry_type") or "").strip()
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
        _status_portfolio_headline_html(
            f"{_city_local_clock_html(str(title))}"
            f"🟡 <b>CLAIM</b> <i>(bot)</i>\n{tg_escape(title)}",
            "CLAIM",
        ),
    )
    log_trade_claim_terminal(str(title))
    balance_after = client.get_portfolio_balance(force_allowance_refresh=False)
    cash_after = float(balance_after.get("cash") or 0)
    mtm_after = float(balance_after.get("positions_market_value") or 0)
    append_trade_csv_row(
        {
            **_trade_csv_base(str(title)),
            **_TRADE_CSV_RESEARCH_PAD,
            **_TRADE_CSV_PLAN_PAD,
            "action": "CLAIM",
            "market_id": market_id,
            "city": _extract_city_from_title(str(title)),
            "entry_type": held_entry_type,
            "decision_reason": "",
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
        send_portfolio_telegram(
            telegram,
            client,
            headline,
            headline_html=headline_html,
            state=state,
            blacklist_ids=get_effective_settings().blacklist_market_ids,
        )
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
    event_id = market_event_id(market)

    if has_position and not trade_row_matches_gamma_market(trade_row, market):
        warn_gamma_trade_mismatch_once(trade_row, market, telegram)
        return

    # --- EXIT LOGIC (for existing positions) ---
    if has_position and status in STATUS_CLOSED:
        claim_position(client, market, state, telegram, state_trade_key=sk)
        return

    # smart engine exit checks (momentum, competitor surge, time-decay)
    if has_position and trade_row and market_can_post_clob_orders(market):
        exit_reason, exit_price = check_exits(
            market,
            trade_row,
            market_id,
            settings,
            event_cache,
            client,
        )
        if exit_reason:
            close_position(
                client,
                market,
                state,
                telegram,
                exit_price or gamma_probability,
                exit_reason,
                settings,
                state_trade_key=sk,
            )
            # no event-level buy cooldown here — it blocked rotating into the surging
            # sibling bucket (same gamma event) for ~20m after competitor-surge exits.
            if exit_reason in (
                "momentum-stop-loss",
                "competitor-surge",
                "momentum-competitor-dominant",
            ):
                churn_on_stop_loss_exit(
                    state,
                    market_id,
                    settings.churn_max_stop_cycles,
                    settings.churn_cooldown_sec,
                )
            return

    # research model flip exit (optional)
    if (
        getattr(settings, "research_exit_on_model_flip", False)
        and has_position
        and trade_row
        and market_can_post_clob_orders(market)
    ):
        t_exit = str(market.get("question") or market.get("title") or "")
        p_exit = parse_highest_temp_title(t_exit)
        if p_exit:
            ow_blend_e = bool(getattr(settings, "enable_openweather_forecast", False))
            ow_key_exit = (
                os.getenv("OPENWEATHER_API_KEY", "").strip() if ow_blend_e else ""
            )
            _om_e, _ow_e, cons_e = get_forecast_max_for_city_day(
                p_exit.city_key,
                p_exit.event_date,
                p_exit.tz_name,
                openweather_api_key=ow_key_exit,
                use_openweather=ow_blend_e,
            )
            if cons_e is not None:
                margin_e = float(
                    getattr(
                        settings,
                        "forecast_contradict_margin_c",
                        FORECAST_CONTRADICT_MARGIN_C,
                    )
                )
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

    # regular stop-loss — use per-type SL (from entry_type stored at buy time)
    entry_type = str((trade_row or {}).get("entry_type") or "normal").strip()
    sl_bar_live = stop_loss_bar_for_entry_type(entry_type, settings)
    prob_stop = stop_loss_reference_if_triggered(market, trade_row, sl_bar_live)
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

    # take-profit
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
                f"EXIT PAUSED: market not accepting CLOB orders\n{t}\nstatus={status!r}\n"
                f"acceptingOrders={market.get('acceptingOrders')}  "
                f"enableOrderBook={market.get('enableOrderBook')}"
            )
            print(term_wrap(TERM_YELLOW, warn))
            try:
                if telegram.is_configured():
                    telegram.send_html_chunks(
                        f"{_telegram_local_clock_html()}"
                        f"⏸️ <b>EXIT PAUSED</b> (CLOB off)\n{tg_escape(t)}\n<pre>{tg_escape(warn)}</pre>"
                    )
            except requests.RequestException:
                pass
            tr["_warned_clob_disabled"] = True
        return

    if has_position:
        active_trades[trade_key].pop("_warned_clob_disabled", None)

    # --- ENTRY LOGIC (new buys only) ---
    if not allow_new_buys or has_position:
        return
    if not market_can_post_clob_orders(market):
        t = str(market.get("question") or market.get("title") or market_id)
        print(
            term_wrap(
                TERM_DIM,
                f"[decision] skip buy — market not tradable on CLOB "
                f"(status={status!r}, acceptingOrders={market.get('acceptingOrders')}, "
                f"enableOrderBook={market.get('enableOrderBook')}, active={market.get('active')})\n  {t}",
            )
        )
        return
    if trade_lock_active(state, "buy_market", market_id):
        return
    if event_id and trade_lock_active(state, "buy_event", event_id):
        return

    _title = str(market.get("question") or market.get("title") or "")

    # time gate: same calendar day in the market city as event_date, and hour window
    _pm_timegate = parse_highest_temp_title(_title)
    time_ok, _city_hour, _time_skip = entry_time_allowed(
        _title,
        earliest_hour=int(settings.buy_earliest_local_hour),
        latest_hour=int(settings.buy_latest_local_hour or 24),
        event_date=_pm_timegate.event_date if _pm_timegate else None,
    )
    if not time_ok:
        print(
            term_wrap(
                TERM_DIM,
                f"[decision] skip buy — local_time_gate {_time_skip} hour={_city_hour!r}\n  {_title}",
            )
        )
        return

    # calendar gate
    if BUY_BLOCK_EVENT_DATE_AFTER_ISRAEL_TODAY:
        pm_cal = _pm_timegate or parse_highest_temp_title(_title)
        if pm_cal is not None:
            israel_today = now_in_report_timezone().date()
            if pm_cal.event_date > israel_today:
                return

    # position count gate
    if len(active_trades) >= MAX_CONCURRENT_POSITIONS:
        return

    wsec = momentum_window_sec(settings)
    rise_thr = float(getattr(settings, "momentum_entry_rise", 0.15))
    mom_sig, mom_rise_pts = momentum_entry_signal(
        market_id,
        rise_threshold=rise_thr,
        window_sec=wsec,
    )
    mom_lo = float(getattr(settings, "momentum_min_price", MOMENTUM_MIN_PRICE))
    mom_hi = float(getattr(settings, "momentum_max_entry", MOMENTUM_MAX_ENTRY))
    dbl_min = float(
        getattr(settings, "double_momentum_min_price", DOUBLE_MOMENTUM_MIN_PRICE)
    )
    dbl_max = float(
        getattr(settings, "double_momentum_max_price", DOUBLE_MOMENTUM_MAX_PRICE)
    )
    dbl_rise = float(
        getattr(settings, "double_momentum_entry_rise", DOUBLE_MOMENTUM_ENTRY_RISE)
    )
    double_momentum_price_ok = (
        mom_rise_pts + 1e-12 >= dbl_rise
        and dbl_min - 1e-12 <= gamma_probability <= dbl_max + 1e-12
    )
    momentum_price_ok = (
        mom_sig and mom_lo - 1e-12 <= gamma_probability <= mom_hi + 1e-12
    ) or double_momentum_price_ok

    sw = detect_momentum_switch(
        market,
        market_id,
        float(gamma_probability),
        state,
        client,
        event_cache,
        settings,
    )
    if sw is not None:
        held_mkt, held_key = sw
        h_t = str(held_mkt.get("question") or held_mkt.get("title") or held_key)
        print(
            term_wrap(
                TERM_DIM,
                f"[momentum_switch] sell held → buy surging sibling\n"
                f"  out: {h_t}\n"
                f"  in:  {_title}",
            )
        )
        close_position(
            client,
            held_mkt,
            state,
            telegram,
            parse_market_probability(held_mkt),
            "momentum-switch-out",
            settings,
            state_trade_key=held_key,
        )
        if (state.get("active_trades") or {}).get(held_key):
            print(
                term_wrap(
                    TERM_YELLOW,
                    "[momentum_switch] held not cleared after sell — skip new buy",
                )
            )
            return

    # price band gate (normal buy band OR momentum surge band)
    band_off = bool(getattr(settings, "buy_disable_price_band", False))
    if not band_off:
        normal_band = settings.buy_min <= gamma_probability <= settings.buy_max
        if not normal_band and not momentum_price_ok:
            print(
                term_wrap(
                    TERM_DIM,
                    f"[decision] skip buy — outside buy_min/buy_max and momentum band\n"
                    f"  {_title}",
                )
            )
            return

    # --- smart decision engine ---
    parsed_fc = parse_highest_temp_title(_title)
    if not parsed_fc:
        return

    ow_blend = bool(getattr(settings, "enable_openweather_forecast", False))
    ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip() if ow_blend else ""
    _om, _ow, cons = get_forecast_max_for_city_day(
        parsed_fc.city_key,
        parsed_fc.event_date,
        parsed_fc.tz_name,
        openweather_api_key=ow_key,
        use_openweather=ow_blend,
    )
    # forecast consensus is optional now (research/calibration removed from decisions).
    # when missing, fall back to a neutral value so price-based gates (band, momentum,
    # competition) still drive entries.
    if cons is None:
        print(
            term_wrap(
                TERM_DIM,
                f"[decision] no forecast consensus for {parsed_fc.city_key} — "
                f"continuing on price-based gates only\n  {_title}",
            )
        )
    cons_for_eval = float(cons) if cons is not None else 0.0

    td = evaluate_entry(
        parsed_fc,
        cons_for_eval,
        float(gamma_probability),
        market,
        client,
        settings,
        state,
        event_cache,
    )

    if td.decision != "BUY":
        print(term_wrap(TERM_DIM, f"[decision] skip buy — {td.reason}\n  {_title}"))
        try:
            if telegram.is_configured() and bool(
                getattr(settings, "decision_skip_telegram_notify", False)
            ):
                cd = float(settings.research_skip_telegram_cooldown_sec)
                if decision_skip_telegram_allowed(market_id, td.reason, cd):
                    det = format_decision_engine_html(td)
                    if td.research is not None:
                        det += format_research_context_html(
                            consensus_c=td.research.consensus_c,
                            implied_yes=td.research.implied_yes,
                            edge=td.research.edge,
                            required_edge=td.research.required_edge,
                            fee_drag=td.research.fee_drag,
                            clob_yes=gamma_probability,
                            edge_raw=td.research.edge_raw,
                            edge_soft_boost=td.research.edge_soft_boost,
                        )
                    telegram.send_html_chunks(
                        f"{_telegram_local_clock_html()}"
                        + format_decision_skip_html(str(_title), td.reason, det)
                    )
        except requests.RequestException:
            pass
        return

    place_buy(
        client,
        market,
        state,
        telegram,
        gamma_probability,
        settings,
        event_cache,
        trade_decision=td,
    )
