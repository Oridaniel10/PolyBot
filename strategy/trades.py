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
    SL_CATEGORY_ABSOLUTE,
    SL_CATEGORY_MOMENTUM,
    SL_CATEGORY_RELATIVE,
    SL_CATEGORY_TRAILING,
    STATUS_CLOSED,
    TAKE_PROFIT_COMPARE_SLACK,
    TERM_CYAN,
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
    churn_event_mark_loss_tiered,
    churn_mark_event_recent_stoploss,
    churn_record_event_leader_switch,
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
    effective_stop_price_for_trade,
    evaluate_entry,
    momentum_window_sec,
    momentum_fast_window_sec,
    double_momentum_fast_window_sec,
    momentum_dual_window_check,
)
from strategy.limit_executor import (
    LimitExecutionResult,
    execute_buy,
)
from strategy.telegram_dedup import (
    categorize_error,
    clear_failed_exit_notices,
    should_send_failed_exit_notice,
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


def entry_type_max_price(entry_type: str, settings: RuntimeSettings) -> float:
    """resolve the max allowed live CLOB price for a buy of this entry type."""
    et = str(entry_type or "normal").strip()
    if et == "double_momentum":
        return float(
            getattr(settings, "double_momentum_max_price", DOUBLE_MOMENTUM_MAX_PRICE)
        )
    if et == "momentum":
        return float(getattr(settings, "momentum_max_entry", MOMENTUM_MAX_ENTRY))
    return float(getattr(settings, "buy_max", 0.84))


def entry_type_min_price(entry_type: str, settings: RuntimeSettings) -> float:
    et = str(entry_type or "normal").strip()
    if et == "double_momentum":
        return float(
            getattr(settings, "double_momentum_min_price", DOUBLE_MOMENTUM_MIN_PRICE)
        )
    if et == "momentum":
        return float(getattr(settings, "momentum_min_price", MOMENTUM_MIN_PRICE))
    return float(getattr(settings, "buy_min", 0.80))


def update_highest_seen_price(trade_row: Dict[str, Any], live_price: float) -> float:
    """track the running peak so trailing stop has a reliable reference.

    returns the updated highest_seen_price.
    """
    px = float(live_price or 0.0)
    if px <= 0:
        return float(trade_row.get("highest_seen_price") or 0.0)
    cur = float(trade_row.get("highest_seen_price") or 0.0)
    if px > cur:
        trade_row["highest_seen_price"] = round(px, 6)
        return px
    return cur


def _trigger_window_label(window: str) -> str:
    if window == "5m_fast":
        return "5m Fast"
    if window == "15m_std":
        return "15m Std"
    if window == "both":
        return "Both Windows"
    return ""


def _entry_type_label(entry_type: str) -> str:
    et = str(entry_type or "normal").strip().lower()
    if et == "double_momentum":
        return "DOUBLE MOMENTUM"
    if et == "momentum":
        return "MOMENTUM"
    return "NORMAL"


def _format_trigger_summary_html(td: TradeDecision) -> str:
    """build the human-readable trigger context for a momentum BUY telegram."""
    if not td or td.entry_type == "normal" or td.trigger_window == "none":
        return ""
    window_label = _trigger_window_label(td.trigger_window)
    abs_pts = float(td.trigger_abs_rise or 0.0)
    pct = float(td.trigger_pct_rise or 0.0)
    pct_str = f"{pct * 100:.0f}%"
    return (
        f"⚡ Triggered by {tg_escape(window_label)} Window "
        f"(<code>+{abs_pts:+.3f} pts / {tg_escape(pct_str)}</code>)\n"
    )


def _format_band_summary_html(entry_type: str, live_price: float, settings: RuntimeSettings) -> str:
    lo = entry_type_min_price(entry_type, settings)
    hi = entry_type_max_price(entry_type, settings)
    label = _entry_type_label(entry_type)
    return (
        f"📈 Entry price <code>{live_price:.4f}</code> within "
        f"{tg_escape(label.title())} Band "
        f"(<code>{lo:.2f}</code>-<code>{hi:.2f}</code>)\n"
    )


def _sl_category_human(category: str) -> str:
    if category == SL_CATEGORY_TRAILING:
        return "Trailing Stop"
    if category == SL_CATEGORY_RELATIVE:
        return "Entry-Drop Stop"
    if category == SL_CATEGORY_ABSOLUTE:
        return "Hard Floor Stop"
    if category == SL_CATEGORY_MOMENTUM:
        return "Momentum Drawdown"
    return ""


def _sell_header_label(reason: str) -> str:
    r = str(reason or "").strip().lower()
    if r in ("stop-loss",):
        return "STOP LOSS"
    if r == "trailing-stop":
        return "TRAILING STOP"
    if r == "momentum-stop-loss":
        return "MOMENTUM STOP LOSS"
    if r == "take-profit":
        return "TAKE PROFIT"
    if r in ("competitor-surge", "peer-yes-surge", "momentum-competitor-dominant"):
        return "COMPETITOR SURGE"
    if r == "time-decay":
        return "TIME DECAY"
    if r == "momentum-switch-out":
        return "MOMENTUM SWITCH OUT"
    if r == "research-model-flip":
        return "RESEARCH FLIP"
    return "SELL"


def _build_full_reason_buy(
    td: Optional[TradeDecision],
    entry_type: str,
    exec_result: "LimitExecutionResult",
) -> str:
    """concatenated single-line reason path for a BUY (used in CSV/logs)."""
    parts: List[str] = []
    parts.append(f"entry_type={entry_type}")
    if td is not None:
        parts.append(f"decision_reason={str(td.reason)[:120]}")
        parts.append(f"trigger_window={td.trigger_window}")
        parts.append(
            f"trigger_metric=abs={float(td.trigger_abs_rise):+.4f},pct={float(td.trigger_pct_rise):+.4f}"
        )
        parts.append(
            f"prices=old={float(td.trigger_old_price):.4f},new={float(td.trigger_new_price):.4f}"
        )
    parts.append(f"exec_mode={exec_result.mode}")
    parts.append(
        f"limit_px={exec_result.limit_price:.4f}|fill_px={exec_result.fill_price:.4f}"
        f"|slip={exec_result.slippage_estimate:+.4f}"
    )
    return " · ".join(parts)[:400]


def _build_full_reason_sell(
    trade: Dict[str, Any],
    reason: str,
    *,
    execution_mode: str,
    fill_price: float,
    limit_price: float,
    settings: Optional[RuntimeSettings] = None,
) -> str:
    parts: List[str] = []
    parts.append(f"reason={reason}")
    cat = str(trade.get("sl_category") or "").strip()
    if cat:
        parts.append(f"sl_category={cat}")
    level = trade.get("sl_level")
    if level is not None:
        parts.append(f"sl_level={float(level):.4f}")
    if settings is not None and reason in ("stop-loss", "trailing-stop"):
        et = str(trade.get("entry_type") or "normal").strip()
        ent = float(trade.get("entry_price") or 0.0)
        peak = float(trade.get("highest_seen_price") or 0.0)
        if ent > 1e-9:
            eff = effective_stop_price_for_trade(
                et, ent, settings, highest_seen_price=peak
            )
            parts.append(f"sl_effective={eff:.4f}")
    peak = float(trade.get("highest_seen_price") or 0.0)
    if peak > 0:
        parts.append(f"max_seen={peak:.4f}")
    dd = trade.get("sl_drawdown_points")
    if dd is not None:
        parts.append(f"dd={float(dd):+.4f}")
    parts.append(f"exec_mode={execution_mode}")
    parts.append(
        f"limit_px={limit_price:.4f}|fill_px={fill_price:.4f}"
    )
    return " · ".join(parts)[:400]


def _bucket_switch_log(step: str, **fields: Any) -> None:
    """structured terminal log for each bucket-switch step.

    keeps every label requested by the strategy hardening doc:
    switch_candidate_detected / switch_sell_started / switch_sell_failed_skip_buy /
    switch_sell_success_buy_new / switch_buy_failed_after_sell / switch_completed.
    """
    parts = [f"step={step}"]
    for key, value in fields.items():
        parts.append(f"{key}={value!r}")
    body = "  ".join(parts)
    print(term_wrap(TERM_CYAN, f"[bucket_switch] {body}"))


def _bucket_switch_telegram(
    telegram: TelegramBot,
    settings: RuntimeSettings,
    *,
    step_label: str,
    target_title: str,
    held_title: str,
    extra: str = "",
) -> None:
    if not bool(getattr(settings, "telegram_verbose_trade_reason", True)):
        return
    if not telegram.is_configured():
        return
    try:
        body = (
            f"{_telegram_local_clock_html()}"
            f"🔁 <b>BUCKET SWITCH</b> · <i>{tg_escape(step_label)}</i>\n"
            f"out: {tg_escape(held_title)}\n"
            f"in:  {tg_escape(target_title)}"
        )
        if extra:
            body += f"\n<pre>{tg_escape(extra)}</pre>"
        telegram.send_html_chunks(body)
    except requests.RequestException:
        pass


def _execute_bucket_switch_sell(
    *,
    client: PolymarketClient,
    telegram: TelegramBot,
    state: Dict[str, Any],
    settings: RuntimeSettings,
    held_market: Dict[str, Any],
    held_key: str,
    target_title: str,
    target_market_id: str,
) -> bool:
    """sell the held bucket atomically before allowing a new BUY in the same event.

    returns True when the held position is cleared (so caller may proceed to
    place_buy on the target). returns False if the sell failed or didn't fully
    clear the position — in that case the caller MUST NOT place a new buy.
    """
    held_title = str(
        held_market.get("question") or held_market.get("title") or held_key
    )
    _bucket_switch_log(
        "switch_candidate_detected",
        held_key=held_key,
        target=target_market_id,
    )
    _bucket_switch_telegram(
        telegram,
        settings,
        step_label="candidate detected",
        target_title=target_title,
        held_title=held_title,
    )
    _bucket_switch_log("switch_sell_started", held_key=held_key)
    _bucket_switch_telegram(
        telegram,
        settings,
        step_label="selling held first",
        target_title=target_title,
        held_title=held_title,
    )
    try:
        close_position(
            client,
            held_market,
            state,
            telegram,
            parse_market_probability(held_market),
            "momentum-switch-out",
            settings,
            state_trade_key=held_key,
        )
    except Exception as err:
        _bucket_switch_log("switch_sell_failed_skip_buy", error=repr(err))
        _bucket_switch_telegram(
            telegram,
            settings,
            step_label="sell failed — skip buy",
            target_title=target_title,
            held_title=held_title,
            extra=repr(err),
        )
        return False
    still_held = (state.get("active_trades") or {}).get(held_key)
    if still_held:
        _bucket_switch_log(
            "switch_buy_failed_after_sell",
            reason="held_not_cleared",
            held_key=held_key,
        )
        _bucket_switch_telegram(
            telegram,
            settings,
            step_label="held not cleared — skip buy",
            target_title=target_title,
            held_title=held_title,
        )
        return False
    _bucket_switch_log(
        "switch_sell_success_buy_new", held_key=held_key, target=target_market_id
    )
    _bucket_switch_telegram(
        telegram,
        settings,
        step_label="sell ok — placing new buy",
        target_title=target_title,
        held_title=held_title,
    )
    return True


def _emit_failed_exit_telegram(
    *,
    telegram: TelegramBot,
    settings: RuntimeSettings,
    market_id: str,
    reason: str,
    title: str,
    sell_shares: float,
    exchange_yes: float,
    mark: float,
    detail: str,
) -> None:
    """send a deduped failed-exit notification.

    suppresses repeats for the same (market_id, reason, error_category)
    inside the configured cooldown so the user gets one alert per failure mode
    instead of every tick. terminal logs continue as before.
    """
    err_cat = categorize_error(detail)
    if not should_send_failed_exit_notice(
        settings,
        market_id=market_id,
        exit_reason=reason,
        error_category=err_cat,
    ):
        print(
            term_wrap(
                TERM_DIM,
                f"[telegram_dedup] failed-exit suppressed market={market_id} "
                f"reason={reason} category={err_cat}",
            )
        )
        return
    if not telegram.is_configured():
        return
    cooldown = int(getattr(settings, "telegram_failed_exit_cooldown_sec", 900))
    print(
        term_wrap(
            TERM_DIM,
            f"[telegram_dedup] failed-exit FIRST notice market={market_id} "
            f"reason={reason} category={err_cat} cooldown={cooldown}s",
        )
    )
    try:
        telegram.send_html_chunks(
            f"{_telegram_local_clock_html()}"
            f"🔴 <b>SELL FAILED</b> <code>{tg_escape(reason)}</code>"
            f"  ·  category <code>{tg_escape(err_cat)}</code>\n"
            f"{tg_escape(title)}\n"
            f"shares <code>{sell_shares:.6f}</code>  ·  exch ~<code>{exchange_yes:.6f}</code>"
            f"  ·  mark <code>{mark:.4f}</code>\n"
            f"<pre>{tg_escape(detail)}</pre>"
        )
    except requests.RequestException:
        pass


def _format_sl_category_html(trade: Dict[str, Any], reason: str) -> str:
    """render a one-line category summary for SELL telegram."""
    if reason not in (
        "stop-loss",
        "trailing-stop",
        "momentum-stop-loss",
    ):
        return ""
    category = str(trade.get("sl_category") or "").strip()
    if not category:
        return ""
    label = _sl_category_human(category)
    if not label:
        return ""
    parts = [f"🧭 SL category <code>{tg_escape(category)}</code> · {tg_escape(label)}"]
    level = trade.get("sl_level")
    if level is not None:
        parts.append(f"trigger <code>{float(level):.4f}</code>")
    peak = float(trade.get("highest_seen_price") or 0.0)
    if peak > 0:
        parts.append(f"max seen <code>{peak:.4f}</code>")
    dd = trade.get("sl_drawdown_points")
    if dd is not None:
        parts.append(f"dd <code>{float(dd):+.4f}</code>")
    eff = trade.get("sl_effective")
    if eff is not None and float(eff) > 0:
        parts.append(f"bar <code>{float(eff):.4f}</code>")
    return " · ".join(parts) + "\n"


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
    decision_price = float(probability or 0.0)
    # always-fresh CLOB best ask — bypass any cached gamma value because price
    # can move between scan and submit on fast-moving buckets.
    live_clob = client.get_clob_best_ask_yes(market)
    if live_clob <= 0:
        live_clob = client.get_clob_yes_price(market)
    entry_type = "normal"
    if trade_decision:
        entry_type = getattr(trade_decision, "entry_type", "normal") or "normal"
    max_allowed = entry_type_max_price(entry_type, settings)
    min_allowed = entry_type_min_price(entry_type, settings)
    band_off = bool(getattr(settings, "buy_disable_price_band", False))
    print(
        term_wrap(
            TERM_DIM,
            f"[buy_safety] entry_type={entry_type} decision={decision_price:.4f} "
            f"live_clob={live_clob:.4f} max_allowed={max_allowed:.4f} "
            f"min_allowed={min_allowed:.4f} band_off={band_off}\n  {title}",
        )
    )
    if not band_off and live_clob > 0:
        if live_clob > max_allowed + 1e-9:
            print(
                term_wrap(
                    TERM_RED,
                    f"[buy_safety] skip buy — live_price_above_entry_type_max "
                    f"live={live_clob:.4f} > max={max_allowed:.4f} "
                    f"type={entry_type} buy_allowed=false\n  {title}",
                )
            )
            return
        if live_clob < min_allowed - 1e-9:
            print(
                term_wrap(
                    TERM_DIM,
                    f"[buy_safety] skip buy — live_price_below_entry_type_min "
                    f"live={live_clob:.4f} < min={min_allowed:.4f} "
                    f"type={entry_type} buy_allowed=false\n  {title}",
                )
            )
            return
    if live_clob > 0:
        probability = live_clob

    # decision engine already validated — use its result
    # momentum/double_momentum bypass the forecast contradiction gate (legacy mom_relax).
    mom_relax = entry_type in ("momentum", "double_momentum")
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
    require_fill = bool(getattr(settings, "require_fill_before_state_buy", True))
    exec_result: Optional[LimitExecutionResult] = None
    try:
        exec_result = execute_buy(
            client=client,
            market=market,
            usd_amount=usd,
            settings=settings,
            decision_price=float(probability),
        )
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

    # log execution outcome before deciding whether to record state
    print(
        term_wrap(
            TERM_CYAN,
            f"[execution] mode={exec_result.mode} filled={exec_result.filled} "
            f"limit_px={exec_result.limit_price:.4f} fill_px={exec_result.fill_price:.4f} "
            f"timeout={exec_result.timeout_sec}s slippage={exec_result.slippage_estimate:+.4f}",
        )
    )
    if not exec_result.filled and require_fill:
        clear_trade_lock(state, "buy_market", market_id)
        if event_id:
            clear_trade_lock(state, "buy_event", event_id)
        print(
            term_wrap(
                TERM_YELLOW,
                f"[execution] buy not filled within timeout — skip recording state "
                f"(mode={exec_result.mode})\n  {title}",
            )
        )
        try:
            if telegram.is_configured() and exec_result.mode != "limit_filled":
                live_px = float(exec_result.live_clob_price_before_order or 0.0)
                live_show = (
                    f"{live_px:.4f}"
                    if live_px > 1e-12
                    else f"(n/a, decision {exec_result.decision_price:.4f})"
                )
                err_tail = ""
                if exec_result.mode == "limit_failed" and (
                    exec_result.error or ""
                ).strip():
                    err_tail = f"\n<pre>{tg_escape(exec_result.error[:280])}</pre>"
                telegram.send_html_chunks(
                    f"{_telegram_local_clock_html()}"
                    f"🟠 <b>BUY UNFILLED</b> <code>{tg_escape(exec_result.mode)}</code>\n"
                    f"{tg_escape(title)}\n"
                    f"limit <code>{exec_result.limit_price:.4f}</code> · "
                    f"timeout <code>{exec_result.timeout_sec}s</code> · "
                    f"live ask (ref) <code>{tg_escape(live_show)}</code>"
                    f"{err_tail}"
                )
        except requests.RequestException:
            pass
        return

    order = exec_result.market_response if exec_result.market_response else {
        "orderID": exec_result.order_id,
    }
    fill_price = float(exec_result.fill_price or probability)
    if fill_price > 0:
        probability = fill_price
    shares = (
        float(exec_result.filled_shares)
        if exec_result.filled_shares > 0
        else (round(usd / probability, 6) if probability else 0.0)
    )
    tp_bar = max(
        0.0,
        min(1.0, float(settings.take_profit) - TAKE_PROFIT_COMPARE_SLACK),
    )
    # effective SL combines per-type floor + entry-relative drop.
    sl_bar = effective_stop_price_for_trade(entry_type, float(probability), settings)
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
        "highest_seen_price": probability,
        "order_ref": order_ref_from_response(order),
        "tp_exit_bar": tp_bar,
        "sl_mark_bar": sl_bar,
        "entry_time_utc": now_in_report_timezone().isoformat(),
        "entry_type": entry_type,
        "trigger_window": (
            getattr(trade_decision, "trigger_window", "none")
            if trade_decision is not None
            else "none"
        ),
        "trigger_abs_rise": (
            float(getattr(trade_decision, "trigger_abs_rise", 0.0))
            if trade_decision is not None
            else 0.0
        ),
        "trigger_pct_rise": (
            float(getattr(trade_decision, "trigger_pct_rise", 0.0))
            if trade_decision is not None
            else 0.0
        ),
        "decision_price": round(decision_price, 6),
        "live_clob_price_before_order": round(
            float(exec_result.live_clob_price_before_order), 6
        ),
        "execution_mode": str(exec_result.mode),
        "execution_limit_price": round(float(exec_result.limit_price), 6),
        "execution_fill_price": round(float(exec_result.fill_price or probability), 6),
        "execution_slippage": round(float(exec_result.slippage_estimate), 6),
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
    et_label = _entry_type_label(entry_type)
    verbose_tg = bool(getattr(settings, "telegram_verbose_trade_reason", True))
    trigger_html = (
        _format_trigger_summary_html(trade_decision)
        if (verbose_tg and trade_decision is not None)
        else ""
    )
    band_html = (
        _format_band_summary_html(entry_type, float(probability), settings)
        if verbose_tg
        else ""
    )
    headline_html = _status_portfolio_headline_html(
        (
            f"{_city_local_clock_html(str(title))}"
            f"🚀 <b>NEW BUY: [{tg_escape(et_label)}]</b> <i>(bot)</i>\n"
            f"{tg_escape(title)}\n"
            f"{trigger_html}{band_html}"
            f"💰 cash <code>${cash:.2f}</code>  ·  reserve <code>${reserve_usd:.2f}</code>"
            f"  ·  tradable <code>${tradable:.2f}</code>\n"
            f"💵 <code>${usd:.2f}</code>  ·  shares ~<code>{shares:.4f}</code>"
            f"  ·  yes ~<code>{probability:.4f}</code>\n"
            f"🔧 exec <code>{tg_escape(exec_result.mode)}</code>"
            f"  ·  limit <code>{exec_result.limit_price:.4f}</code>"
            f"  ·  fill <code>{exec_result.fill_price:.4f}</code>"
            f"  ·  slip <code>{exec_result.slippage_estimate:+.4f}</code>\n"
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
    full_reason_buy = _build_full_reason_buy(trade_decision, entry_type, exec_result)
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
        "trigger_window": (
            getattr(trade_decision, "trigger_window", "none")
            if trade_decision is not None
            else "none"
        ),
        "trigger_abs_rise": round(
            float(getattr(trade_decision, "trigger_abs_rise", 0.0)) if trade_decision else 0.0,
            6,
        ),
        "trigger_pct_rise": round(
            float(getattr(trade_decision, "trigger_pct_rise", 0.0)) if trade_decision else 0.0,
            6,
        ),
        "decision_price": round(decision_price, 6),
        "live_clob_price_before_order": round(
            float(exec_result.live_clob_price_before_order), 6
        ),
        "execution_mode": str(exec_result.mode),
        "execution_limit_price": round(float(exec_result.limit_price), 6),
        "execution_fill_price": round(float(exec_result.fill_price or probability), 6),
        "execution_slippage": round(float(exec_result.slippage_estimate), 6),
        "sl_category": "",
        "highest_seen_price": round(float(probability), 6),
        "full_reason": full_reason_buy,
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
    # ensure SL category is set even when called outside check_exits/fast_watcher.
    if reason in ("stop-loss", "trailing-stop") and not trade.get("sl_category"):
        try:
            from strategy.decision_core import classify_stop_loss_breach

            breach = classify_stop_loss_breach(
                held_entry_type or "normal",
                entry,
                float(probability),
                settings,
                highest_seen_price=float(trade.get("highest_seen_price") or 0.0),
            )
            if breach is not None:
                category, level = breach
                trade["sl_category"] = category
                trade["sl_level"] = round(float(level), 6)
                trade["sl_effective"] = round(
                    float(
                        effective_stop_price_for_trade(
                            held_entry_type or "normal",
                            entry,
                            settings,
                            highest_seen_price=float(
                                trade.get("highest_seen_price") or 0.0
                            ),
                        )
                    ),
                    6,
                )
        except Exception:
            pass
    elif reason == "momentum-stop-loss" and not trade.get("sl_category"):
        trade["sl_category"] = SL_CATEGORY_MOMENTUM
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
        detail = str(err.error_msg) if err.error_msg is not None else str(err)
        log_trade_sell_terminal(
            str(title), reason, sell_shares, entry, probability, failed=True
        )
        print(term_wrap(TERM_RED, f"  detail: {err}\n  {detail}"))
        _emit_failed_exit_telegram(
            telegram=telegram,
            settings=settings,
            market_id=market_id,
            reason=reason,
            title=str(title),
            sell_shares=float(sell_shares),
            exchange_yes=float(exchange_yes),
            mark=float(probability),
            detail=detail,
        )
        return
    except Exception as err:
        clear_trade_lock(state, "sell_market", market_id)
        detail = repr(err)
        log_trade_sell_terminal(
            str(title), reason, sell_shares, entry, probability, failed=True
        )
        print(term_wrap(TERM_RED, f"  detail: {detail}"))
        _emit_failed_exit_telegram(
            telegram=telegram,
            settings=settings,
            market_id=market_id,
            reason=reason,
            title=str(title),
            sell_shares=float(sell_shares),
            exchange_yes=float(exchange_yes),
            mark=float(probability),
            detail=detail,
        )
        return

    # sell finally went through — clear any lingering failed-exit dedup state
    clear_failed_exit_notices(market_id)
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
                "sl_category": str(trade.get("sl_category") or ""),
                "highest_seen_price": round(
                    float(trade.get("highest_seen_price") or 0.0), 6
                ),
                "execution_mode": "limit_gtc",
                "execution_limit_price": round(float(limit_px), 6),
                "full_reason": _build_full_reason_sell(
                    trade,
                    reason,
                    execution_mode="limit_gtc",
                    fill_price=float(probability),
                    limit_price=float(limit_px),
                    settings=settings,
                ),
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
    sell_header_label = _sell_header_label(reason)
    sl_cat_line = (
        _format_sl_category_html(trade, reason)
        if bool(getattr(settings, "telegram_verbose_trade_reason", True))
        else ""
    )
    headline = (
        f"TRADE: {sell_header_label} ({reason})\n"
        f"{title}\n"
        f"shares={sell_shares:.6f}  entry~={entry:.4f}  mark~={probability:.4f}\n"
        f"---\n{summary}"
    )
    est_pnl = (probability - entry) * sell_shares
    headline_html = _status_portfolio_headline_html(
        (
            f"{_city_local_clock_html(str(title))}"
            f"🔴 <b>SELL: [{tg_escape(sell_header_label)}]</b> <code>{tg_escape(reason)}</code> <i>(bot)</i>\n"
            f"{tg_escape(title)}\n"
            f"{sl_cat_line}"
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
    sell_exec_mode = (
        "market"
        if (result.get("sell_execution") or "").lower() == "market"
        else (result.get("sell_execution") or "")
    )
    full_reason_sell = _build_full_reason_sell(
        trade,
        reason,
        execution_mode=str(sell_exec_mode or "market"),
        fill_price=float(probability),
        limit_price=float(result.get("limit_price") or 0.0),
        settings=settings,
    )
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
            "sl_category": str(trade.get("sl_category") or ""),
            "highest_seen_price": round(float(trade.get("highest_seen_price") or 0.0), 6),
            "execution_mode": str(sell_exec_mode or "market"),
            "execution_fill_price": round(float(probability), 6),
            "execution_limit_price": round(float(result.get("limit_price") or 0.0), 6),
            "full_reason": full_reason_sell,
        }
    )
    if reason in (
        "stop-loss",
        "trailing-stop",
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
        # event-level churn: tiered cooldowns + immediate sibling block after a loss
        try:
            from polymarket_client import gamma_event_ids_for_market

            eids = gamma_event_ids_for_market(market)
            if eids:
                event_id = str(eids[0]).strip()
                churn_event_mark_loss_tiered(
                    state,
                    event_id,
                    int(getattr(settings, "churn_event_loss_1_cooldown_sec", 0)),
                    int(getattr(settings, "churn_event_loss_2_cooldown_sec", 0)),
                )
                churn_mark_event_recent_stoploss(
                    state,
                    event_id,
                    int(getattr(settings, "churn_event_loss_1_cooldown_sec", 0)),
                )
        except Exception:
            pass
    elif reason == "momentum-switch-out":
        # repeated sibling rotations in a short window mark event unstable.
        try:
            from polymarket_client import gamma_event_ids_for_market

            eids = gamma_event_ids_for_market(market)
            if eids:
                churn_record_event_leader_switch(
                    state=state,
                    event_id=str(eids[0]).strip(),
                    now_ts=time.time(),
                    window_sec=int(getattr(settings, "leader_switch_window_sec", 600)),
                    max_count=int(getattr(settings, "leader_switch_max_count", 3)),
                    unstable_cooldown_sec=int(
                        getattr(settings, "unstable_event_cooldown_sec", 1800)
                    ),
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

    # keep highest_seen_price fresh on every tick the slow loop sees a new price.
    # the fast watcher does the same against live CLOB; either path keeps the
    # trailing stop reference up to date so we can lock in profit reliably.
    if has_position and trade_row is not None:
        live_for_peak = float(trade_row.get("last_price") or 0.0) or float(
            gamma_probability
        )
        if live_for_peak > 0:
            update_highest_seen_price(trade_row, live_for_peak)

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
    entry_price_live = float((trade_row or {}).get("entry_price") or 0.0)
    sl_bar_live = effective_stop_price_for_trade(entry_type, entry_price_live, settings)
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
    fast_wsec = momentum_fast_window_sec(settings)
    rise_thr = float(getattr(settings, "momentum_entry_rise", 0.15))
    rise_pct_thr = float(getattr(settings, "momentum_pct_rise", 0.35))
    mom_start_min = float(getattr(settings, "momentum_min_start_price", 0.10))
    mom_lo = float(getattr(settings, "momentum_min_price", MOMENTUM_MIN_PRICE))
    mom_hi = float(getattr(settings, "momentum_max_entry", MOMENTUM_MAX_ENTRY))
    # check both 15m and 5m windows — either passing qualifies the bucket
    mom_sig, _mom_trigger, _mom_meta = momentum_dual_window_check(
        market_id=market_id,
        abs_rise_threshold=rise_thr,
        pct_rise_threshold=rise_pct_thr,
        std_window_sec=wsec,
        fast_window_sec=fast_wsec,
        min_start_price=mom_start_min,
        min_current_price=mom_lo,
        max_current_price=mom_hi,
    )
    dbl_min = float(
        getattr(settings, "double_momentum_min_price", DOUBLE_MOMENTUM_MIN_PRICE)
    )
    dbl_max = float(
        getattr(settings, "double_momentum_max_price", DOUBLE_MOMENTUM_MAX_PRICE)
    )
    dbl_rise = float(
        getattr(settings, "double_momentum_entry_rise", DOUBLE_MOMENTUM_ENTRY_RISE)
    )
    dbl_pct_rise = float(getattr(settings, "double_momentum_pct_rise", 0.80))
    dbl_start_min = float(getattr(settings, "double_momentum_min_start_price", 0.05))
    dbl_fast_wsec = double_momentum_fast_window_sec(settings)
    dbl_sig, _dbl_trigger, _dbl_meta = momentum_dual_window_check(
        market_id=market_id,
        abs_rise_threshold=dbl_rise,
        pct_rise_threshold=dbl_pct_rise,
        std_window_sec=wsec,
        fast_window_sec=dbl_fast_wsec,
        min_start_price=dbl_start_min,
        min_current_price=dbl_min,
        max_current_price=dbl_max,
    )
    double_momentum_price_ok = bool(dbl_sig)
    # guardrail: rise condition AND price band must both pass for momentum entry
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
        cleared = _execute_bucket_switch_sell(
            client=client,
            telegram=telegram,
            state=state,
            settings=settings,
            held_market=held_mkt,
            held_key=held_key,
            target_title=str(_title),
            target_market_id=market_id,
        )
        if not cleared:
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
    if sw is not None:
        # switch_completed if the buy actually recorded a position; otherwise
        # we already logged switch_buy_failed_after_sell elsewhere.
        if (state.get("active_trades") or {}).get(market_id):
            _bucket_switch_log(
                "switch_completed", target=market_id, held_key=str(sw[1])
            )
            _bucket_switch_telegram(
                telegram,
                settings,
                step_label="switch completed",
                target_title=str(_title),
                held_title=str(
                    sw[0].get("question") or sw[0].get("title") or sw[1]
                ),
            )
        else:
            _bucket_switch_log(
                "switch_buy_failed_after_sell",
                target=market_id,
                reason="buy_did_not_record",
            )
