import time
from typing import Any, Dict, List

from config.constants import MANUAL_SYNC_MIN_ENTRY_PRICE
from notifications.post_buy_plot import schedule_post_buy_event_chart
from polymarket_client import PolymarketClient, condition_ids_equivalent
from state.pnl_ledger import append_trade_csv_row
from strategy.city_tz import city_local_time_str
from strategy.time_utils import format_report_local_hhmm

from strategy.time_utils import now_in_report_timezone


def normalize_positions(positions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    normalized: Dict[str, Dict[str, Any]] = {}
    for position in positions:
        market_id = str(
            position.get("market_id") or position.get("marketId") or ""
        ).strip()
        condition_id = str(position.get("condition_id") or "").strip()
        key = market_id or condition_id
        if not key:
            continue
        normalized[key] = position
    return normalized


def prune_recent_sells(state: Dict[str, Any]) -> Dict[str, Any]:
    recent = state.get("recent_sells") or {}
    if not isinstance(recent, dict):
        state["recent_sells"] = {}
        return {}
    bucket = recent.get("market") or {}
    if not isinstance(bucket, dict):
        recent["market"] = {}
        return recent
    now = time.time()
    for market_id, until in list(bucket.items()):
        if now >= float(until or 0.0):
            bucket.pop(market_id, None)
    return recent


def recent_sell_active(state: Dict[str, Any], market_id: str) -> bool:
    if not market_id:
        return False
    recent = prune_recent_sells(state)
    bucket = recent.get("market") if isinstance(recent, dict) else {}
    if not isinstance(bucket, dict):
        return False
    return time.time() < float(bucket.get(market_id) or 0.0)


def build_manual_trade_log_row(
    *,
    action: str,
    market_id: str,
    title: str,
    entry_type: str,
    price: float,
    shares: float,
    usd: float,
    reason: str,
    entry_price: float,
    pnl_usd: float,
) -> Dict[str, Any]:
    now = now_in_report_timezone()
    safe_title = str(title or "")
    return {
        "timestamp": now.isoformat(),
        "local_hhmm": format_report_local_hhmm(now),
        "city_local_hhmm": city_local_time_str(safe_title),
        "entry_type": str(entry_type or ""),
        "action": action,
        "market_id": str(market_id or ""),
        "market_title": safe_title[:220],
        "city": "",
        "price": round(float(price or 0.0), 4),
        "shares": round(float(shares or 0.0), 4),
        "usd": round(float(usd or 0.0), 2),
        "reason": reason,
        "decision_reason": reason,
        "entry_price": round(float(entry_price or 0.0), 4),
        "pnl_usd": round(float(pnl_usd or 0.0), 2),
        "cash_after": "",
        "positions_mtm": "",
        "total_value": "",
        "consensus_c": "",
        "implied_yes": "",
        "edge": "",
        "required_edge": "",
        "fee_drag": "",
        "tp_exit_bar": "",
        "sl_mark_bar": "",
        "buy_est_tp_pnl_usd": "",
        "buy_est_sl_pnl_usd": "",
        "buy_est_yes_resolve_pnl_usd": "",
    }


def tracked_trade_still_open(
    tracked_row: Dict[str, Any], active_trades: Dict[str, Any]
) -> bool:
    tid = str(tracked_row.get("market_id") or "").strip()
    tcid = str(tracked_row.get("condition_id") or "").strip()
    if tid and tid in active_trades:
        return True
    for row in active_trades.values():
        mid = str(row.get("market_id") or "").strip()
        cid = str(row.get("condition_id") or "").strip()
        if tid and mid and tid == mid:
            return True
        if tcid and cid and condition_ids_equivalent(tcid, cid):
            return True
    return False


def sync_state_with_portfolio(
    client: PolymarketClient, state: Dict[str, Any], telegram: Any = None
) -> Dict[str, Any]:
    positions = client.get_open_positions()
    normalized_positions = normalize_positions(positions)
    tracked = state.get("active_trades", {})

    active_trades: Dict[str, Any] = {}
    for tid, details in tracked.items():
        if tid in normalized_positions:
            active_trades[tid] = details
            continue
        tcid = str(details.get("condition_id") or "")
        if tcid:
            for npos in normalized_positions.values():
                nc = str(npos.get("condition_id") or "")
                if nc and condition_ids_equivalent(nc, tcid):
                    active_trades[tid] = details
                    break

    for pos_key, position in normalized_positions.items():
        mid = str(position.get("market_id") or position.get("marketId") or "").strip()
        cid = str(position.get("condition_id") or "").strip()
        if recent_sell_active(state, mid or pos_key):
            continue
        state_key = mid or pos_key

        prev = active_trades.get(state_key, {})
        if not prev:
            prev = active_trades.get(pos_key, {})
        if not prev and cid:
            prev = active_trades.get(cid, {})
        if not prev and cid:
            for tk, tv in tracked.items():
                tc = str(tv.get("condition_id") or "")
                if tc and condition_ids_equivalent(tc, cid):
                    prev = tv
                    break

        size = float(position.get("size", 0.0) or 0.0)
        avg_px = float(position.get("avg_price", 0.0) or 0.0)
        cur_px = float(position.get("cur_price", 0.0) or 0.0)
        pos_title = str(position.get("title") or "").strip()
        prev_ep = float(prev.get("entry_price") or 0.0)
        # data-api avg_price can diverge from the bot fill (blended legs, rounding).
        # keep ledger-quality entry when we're merging an existing bot row.
        if prev and prev_ep > 1e-12:
            merged_entry_px = prev_ep
        else:
            merged_entry_px = avg_px if avg_px > 1e-12 else prev_ep
        row = {
            "market_id": mid or str(prev.get("market_id") or ""),
            "condition_id": cid or str(prev.get("condition_id") or ""),
            "position_title": pos_title
            or str(prev.get("position_title") or "").strip(),
            "shares": size,
            "last_action": prev.get("last_action", "sync"),
            "entry_price": merged_entry_px,
            # always use data-api mark (0 is valid); never replace 0 with avg — that hides TP/stop
            "last_price": cur_px,
            "order_ref": prev.get("order_ref", ""),
        }
        if not prev:
            # skip dust positions — avg_price near zero is a sync artifact, not a real trade
            if avg_px < MANUAL_SYNC_MIN_ENTRY_PRICE:
                continue
            row["last_action"] = "manual_sync_open"
            append_trade_csv_row(
                build_manual_trade_log_row(
                    action="BUY",
                    market_id=mid or pos_key,
                    title=pos_title,
                    entry_type=str(row.get("entry_type") or "manual"),
                    price=avg_px,
                    shares=size,
                    usd=(avg_px * size) if avg_px > 0 and size > 0 else 0.0,
                    reason="manual_trade_from_site",
                    entry_price=avg_px,
                    pnl_usd=0.0,
                )
            )
            if (
                telegram
                and hasattr(telegram, "is_configured")
                and telegram.is_configured()
            ):
                try:
                    telegram.send_html_chunks(
                        f"⚡ <b>MANUAL BUY DETECTED</b>\n"
                        f"{pos_title}\n"
                        f"shares <code>{size:.4f}</code> · avg <code>{avg_px:.4f}</code>"
                    )
                except Exception:
                    pass
                try:
                    schedule_post_buy_event_chart(
                        client,
                        telegram,
                        mid or pos_key,
                        context="MANUAL",
                    )
                except Exception:
                    pass
        bmin = float(prev.get("_below_min_order_size") or 0)
        until = float(prev.get("_skip_sell_below_min_until") or 0)
        if bmin > 1e-9 and size + 1e-6 < bmin and until > time.time():
            row["_skip_sell_below_min_until"] = until
            row["_below_min_order_size"] = bmin
        if prev.get("_warned_gamma_trade_mismatch"):
            row["_warned_gamma_trade_mismatch"] = prev["_warned_gamma_trade_mismatch"]
        tg_nb = float(prev.get("_below_min_tg_not_before") or 0.0)
        if tg_nb > time.time():
            row["_below_min_tg_not_before"] = tg_nb
        if prev.get("_bypass_min_cooldown_logged"):
            row["_bypass_min_cooldown_logged"] = prev["_bypass_min_cooldown_logged"]
        pend = prev.get("pending_limit_sell_order_id")
        if pend:
            row["pending_limit_sell_order_id"] = pend
            if prev.get("pending_limit_sell_price") is not None:
                row["pending_limit_sell_price"] = prev.get("pending_limit_sell_price")
        for key in [
            "entry_type",
            "entry_time_utc",
            "tp_exit_bar",
            "sl_mark_bar",
            "trigger_window",
            "trigger_abs_rise",
            "trigger_pct_rise",
            "execution_mode",
            "execution_limit_price",
            "execution_fill_price",
            "execution_slippage",
            "highest_seen_price",
        ]:
            if prev.get(key) is not None:
                row[key] = prev[key]

        hp = float(row.get("entry_price") or merged_entry_px or 0.0)
        lp = float(row.get("last_price") or cur_px or 0.0)
        hs = float(row.get("highest_seen_price") or 0.0)
        row["highest_seen_price"] = round(max(hs, hp, lp), 6)

        if not row.get("entry_type"):
            if prev:
                et_prev = str(prev.get("entry_type") or "").strip()
                row["entry_type"] = (
                    et_prev
                    if et_prev
                    else (
                        "normal"
                        if str(prev.get("last_action") or "").lower() == "buy"
                        else "manual"
                    )
                )
            else:
                row["entry_type"] = "manual"
        active_trades[state_key] = row

    for tid, details in tracked.items():
        if tracked_trade_still_open(details, active_trades):
            continue
        title = str(details.get("position_title") or "").strip()
        shares = float(details.get("shares") or 0.0)
        entry = float(details.get("entry_price") or 0.0)
        mark = float(details.get("last_price") or 0.0)
        if shares <= 0:
            continue
        append_trade_csv_row(
            build_manual_trade_log_row(
                action="SELL",
                market_id=str(details.get("market_id") or tid),
                title=title,
                entry_type=str(details.get("entry_type") or "manual"),
                price=mark,
                shares=shares,
                usd=(mark * shares) if mark > 0 else 0.0,
                reason="manual_trade_from_site",
                entry_price=entry,
                pnl_usd=((mark - entry) * shares) if mark > 0 and entry > 0 else 0.0,
            )
        )
        if telegram and hasattr(telegram, "is_configured") and telegram.is_configured():
            try:
                telegram.send_html_chunks(
                    f"⚡ <b>MANUAL SELL DETECTED</b>\n"
                    f"{title}\n"
                    f"closed <code>{shares:.4f}</code> shares"
                )
            except Exception:
                pass

    state["active_trades"] = active_trades
    state["last_sync_at"] = now_in_report_timezone().isoformat()
    return state
