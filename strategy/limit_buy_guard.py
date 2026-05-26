"""Track and block duplicate GTC limit buys for the same market."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from polymarket_client import (
    PolymarketClient,
    order_fully_filled,
    order_resting_unfilled,
    order_terminal_cancelled,
)

PENDING_LIMIT_BUYS_KEY = "pending_limit_buy_orders"
LEGACY_ORPHAN_KEY = "orphan_limit_buy_orders"


def migrate_legacy_orphan_limit_buys(state: Dict[str, Any]) -> None:
    legacy = state.pop(LEGACY_ORPHAN_KEY, None)
    if not isinstance(legacy, dict) or not legacy:
        return
    raw = state.setdefault(PENDING_LIMIT_BUYS_KEY, {})
    if not isinstance(raw, dict):
        state[PENDING_LIMIT_BUYS_KEY] = {}
        raw = state[PENDING_LIMIT_BUYS_KEY]
    bucket = raw
    for market_id, oid in legacy.items():
        mid = str(market_id or "").strip()
        oid_s = str(oid or "").strip()
        if not mid or not oid_s:
            continue
        rows = bucket.setdefault(mid, [])
        if not any(str(r.get("order_id") or "") == oid_s for r in rows):
            rows.append({"order_id": oid_s, "ts": time.time()})


def _pending_bucket(state: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    migrate_legacy_orphan_limit_buys(state)
    raw = state.setdefault(PENDING_LIMIT_BUYS_KEY, {})
    if not isinstance(raw, dict):
        state[PENDING_LIMIT_BUYS_KEY] = {}
        raw = state[PENDING_LIMIT_BUYS_KEY]
    return raw


def record_pending_limit_buy(
    state: Dict[str, Any],
    *,
    market_id: str,
    order_id: str,
    limit_price: float,
    usd: float,
    title: str,
) -> None:
    mid = str(market_id or "").strip()
    oid = str(order_id or "").strip()
    if not mid or not oid:
        return
    bucket = _pending_bucket(state)
    rows = bucket.setdefault(mid, [])
    for row in rows:
        if str(row.get("order_id") or "") == oid:
            row["limit_price"] = float(limit_price)
            row["usd"] = float(usd)
            row["title"] = str(title or "")[:220]
            row["ts"] = float(row.get("ts") or time.time())
            return
    rows.append(
        {
            "order_id": oid,
            "limit_price": float(limit_price),
            "usd": float(usd),
            "title": str(title or "")[:220],
            "ts": time.time(),
        }
    )


def clear_pending_limit_buys(state: Dict[str, Any], market_id: str) -> None:
    mid = str(market_id or "").strip()
    if not mid:
        return
    bucket = _pending_bucket(state)
    bucket.pop(mid, None)


def pending_limit_buy_rows(state: Dict[str, Any], market_id: str) -> List[Dict[str, Any]]:
    mid = str(market_id or "").strip()
    if not mid:
        return []
    bucket = _pending_bucket(state)
    rows = bucket.get(mid)
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _prune_terminal_orders(
    client: PolymarketClient, market_id: str, state: Dict[str, Any]
) -> List[Dict[str, Any]]:
    mid = str(market_id or "").strip()
    rows = pending_limit_buy_rows(state, mid)
    if not rows:
        return []
    live: List[Dict[str, Any]] = []
    for row in rows:
        oid = str(row.get("order_id") or "").strip()
        if not oid:
            continue
        try:
            st = client.get_order_state(oid) or {}
        except Exception:
            st = {}
        if order_fully_filled(st) or order_terminal_cancelled(st):
            continue
        if order_resting_unfilled(st):
            live.append({**row, "order_state": st})
    bucket = _pending_bucket(state)
    if live:
        bucket[mid] = [{k: v for k, v in r.items() if k != "order_state"} for r in live]
    else:
        bucket.pop(mid, None)
    return live


def cancel_resting_limit_buys_for_market(
    client: PolymarketClient, market_id: str, state: Dict[str, Any]
) -> bool:
    """
    cancel all tracked resting buy limits for market_id.

    returns:
    - true if safe to post a new buy (no resting order left).
    """
    mid = str(market_id or "").strip()
    if not mid:
        return True
    rows = pending_limit_buy_rows(state, mid)
    if not rows:
        return True
    for row in rows:
        oid = str(row.get("order_id") or "").strip()
        if not oid:
            continue
        for _ in range(20):
            client.cancel_order(oid)
            try:
                st = client.get_order_state(oid) or {}
            except Exception:
                st = {}
            if order_fully_filled(st):
                break
            if not order_resting_unfilled(st):
                break
            time.sleep(0.25)
    live = _prune_terminal_orders(client, mid, state)
    return len(live) == 0


def resting_limit_buy_blocks_entry(
    client: PolymarketClient, market_id: str, state: Dict[str, Any]
) -> Tuple[bool, str]:
    """true + reason when a resting GTC buy still exists for this market."""
    mid = str(market_id or "").strip()
    if not mid:
        return False, ""
    live = _prune_terminal_orders(client, mid, state)
    if not live:
        return False, ""
    oids = ",".join(str(r.get("order_id") or "")[:12] for r in live[:3])
    return True, f"open_limit_buy_resting order_ids={oids}"


def flush_pending_limit_buys(
    client: PolymarketClient, state: Dict[str, Any]
) -> None:
    """each scan: drop filled/cancelled rows; retry cancel on stubborn rests."""
    bucket = _pending_bucket(state)
    for mid in list(bucket.keys()):
        cancel_resting_limit_buys_for_market(client, str(mid), state)


def collect_open_limit_buys_for_display(
    client: PolymarketClient, state: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """rows for telegram STATUS: resting GTC buys with limit, usd, mark."""
    migrate_legacy_orphan_limit_buys(state)
    bucket = _pending_bucket(state)
    out: List[Dict[str, Any]] = []
    for market_id, rows in list(bucket.items()):
        if not isinstance(rows, list):
            continue
        live = _prune_terminal_orders(client, str(market_id), state)
        for row in live:
            mid = str(market_id)
            title = str(row.get("title") or mid)
            limit_px = float(row.get("limit_price") or 0.0)
            usd = float(row.get("usd") or 0.0)
            mark = 0.0
            try:
                mkt = client.get_market_by_id(mid)
                if mkt:
                    mark = float(client.get_clob_best_ask_yes(mkt) or 0.0)
                    if mark <= 0:
                        mark = float(client.get_clob_yes_price(mkt) or 0.0)
            except Exception:
                pass
            out.append(
                {
                    "market_id": mid,
                    "title": title,
                    "order_id": str(row.get("order_id") or ""),
                    "limit_price": limit_px,
                    "usd": usd,
                    "mark": mark,
                }
            )
    return out
