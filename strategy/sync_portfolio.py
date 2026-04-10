import time
from typing import Any, Dict, List

from polymarket_client import PolymarketClient, condition_ids_equivalent

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


def sync_state_with_portfolio(
    client: PolymarketClient, state: Dict[str, Any]
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
        row = {
            "market_id": mid or str(prev.get("market_id") or ""),
            "condition_id": cid or str(prev.get("condition_id") or ""),
            "position_title": pos_title
            or str(prev.get("position_title") or "").strip(),
            "shares": size,
            "last_action": prev.get("last_action", "sync"),
            "entry_price": avg_px or float(prev.get("entry_price", 0.0) or 0.0),
            # always use data-api mark (0 is valid); never replace 0 with avg — that hides TP/stop
            "last_price": cur_px,
            "order_ref": prev.get("order_ref", ""),
        }
        bmin = float(prev.get("_below_min_order_size") or 0)
        until = float(prev.get("_skip_sell_below_min_until") or 0)
        if bmin > 1e-9 and size + 1e-6 < bmin and until > time.time():
            row["_skip_sell_below_min_until"] = until
            row["_below_min_order_size"] = bmin
        if prev.get("_warned_gamma_trade_mismatch"):
            row["_warned_gamma_trade_mismatch"] = prev["_warned_gamma_trade_mismatch"]
        pend = prev.get("pending_limit_sell_order_id")
        if pend:
            row["pending_limit_sell_order_id"] = pend
            if prev.get("pending_limit_sell_price") is not None:
                row["pending_limit_sell_price"] = prev.get("pending_limit_sell_price")
        active_trades[state_key] = row

    state["active_trades"] = active_trades
    state["last_sync_at"] = now_in_report_timezone().isoformat()
    return state
