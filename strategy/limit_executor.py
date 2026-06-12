"""Limit-order-first execution helpers.

Wraps the polymarket client so callers (place_buy / close_position) can:
1. Submit a GTC limit buy at controlled price and wait for fill (with timeout).
2. Choose between limit-first vs market-first sells based on emergency context.
3. Cleanly cancel unfilled orders so we never leave stale rest orders.

Keeps execution decisions in one place so logs/telegram have consistent labels.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict

from config import constants as C
from config.settings import RuntimeSettings
from polymarket_client import (
    PolymarketClient,
    order_fully_filled,
    order_resting_unfilled,
)

# poll cadence for an open limit order while we wait for the fill timeout.
_LIMIT_POLL_INTERVAL_SEC = 0.5
# minimum tick we expect from CLOB so offset math stays sane.
_DEFAULT_TICK = 0.01
# floors for buy/sell prices so tick math never breaches the [0.01, 0.99] band.
_PRICE_FLOOR = 0.01
_PRICE_CEILING = 0.99


@dataclass
class LimitExecutionResult:
    """outcome of a limit-order-first attempt for either buy or sell."""

    mode: str  # "limit_filled" / "limit_cancelled" / "limit_unsupported" / "market" / "market_skipped" / "limit_failed"
    filled: bool
    fill_price: float = 0.0
    filled_shares: float = 0.0
    order_id: str = ""
    decision_price: float = 0.0
    live_clob_price_before_order: float = 0.0
    limit_price: float = 0.0
    timeout_sec: int = 0
    market_response: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    slippage_estimate: float = 0.0
    # set when cancel+poll could not clear a GTC rest order from the book (UI may show open).
    stale_exchange_order: bool = False


def is_emergency_sell_reason(reason: str) -> bool:
    """check whether a sell reason should bypass the limit-first path."""
    return str(reason or "").strip() in C.EMERGENCY_SELL_REASONS


def limit_orders_active(settings: RuntimeSettings) -> bool:
    return bool(getattr(settings, "limit_orders_enabled", True))


def emergency_market_allowed(settings: RuntimeSettings) -> bool:
    return bool(getattr(settings, "emergency_exit_allow_market_order", True))


def _clamp_price(px: float) -> float:
    if px <= 0:
        return _PRICE_FLOOR
    return max(_PRICE_FLOOR, min(_PRICE_CEILING, float(px)))


def _effective_best_ask_yes(
    client: PolymarketClient, market: Dict[str, Any], decision_price: float
) -> float:
    """best ask from CLOB, or decision_price when book is empty / API returns 0."""
    raw = float(client.get_clob_best_ask_yes(market) or 0.0)
    dec = float(decision_price or 0.0)
    if raw > 1e-12:
        return raw
    if dec > 1e-12:
        return dec
    return 0.0


def _resolve_buy_limit_price(
    client: PolymarketClient,
    market: Dict[str, Any],
    settings: RuntimeSettings,
    decision_price: float,
) -> float:
    """live best-ask + offset, clamped to a valid tick."""
    base = _effective_best_ask_yes(client, market, decision_price)
    offset = float(getattr(settings, "buy_limit_price_offset", 0.0))
    raw = base + offset
    return _clamp_price(raw)


def execute_buy(
    *,
    client: PolymarketClient,
    market: Dict[str, Any],
    usd_amount: float,
    settings: RuntimeSettings,
    decision_price: float,
) -> LimitExecutionResult:
    """submit a buy and wait for fill.

    when limit orders are enabled: post GTC limit at best_ask+offset, poll up
    to buy_limit_order_timeout_sec, then cancel if still unfilled.
    when disabled: go straight to market order (legacy behavior).
    """
    # hard cap: never submit an order above the constant ceiling, regardless of settings
    hard_cap = float(C.MAX_BUY_NOTIONAL_USD)
    soft_cap = float(getattr(settings, "max_buy_notional_usd", hard_cap) or hard_cap)
    usd_amount = min(float(usd_amount), soft_cap, hard_cap)
    timeout_sec = int(getattr(settings, "buy_limit_order_timeout_sec", 8))
    live_clob_before = _effective_best_ask_yes(client, market, float(decision_price))

    if not limit_orders_active(settings):
        out = client.place_market_buy_yes(market, usd_amount)
        return LimitExecutionResult(
            mode="market",
            filled=True,
            fill_price=float(decision_price),
            filled_shares=float(usd_amount) / max(decision_price, 1e-9),
            order_id=_extract_order_id(out),
            decision_price=float(decision_price),
            live_clob_price_before_order=float(live_clob_before),
            limit_price=0.0,
            timeout_sec=0,
            market_response=out if isinstance(out, dict) else {"raw": out},
        )

    limit_px = _resolve_buy_limit_price(client, market, settings, decision_price)
    try:
        posted = client.place_limit_buy_yes(market, usd_amount, limit_px)
    except Exception as err:
        return LimitExecutionResult(
            mode="limit_failed",
            filled=False,
            decision_price=float(decision_price),
            live_clob_price_before_order=float(live_clob_before),
            limit_price=float(limit_px),
            timeout_sec=int(timeout_sec),
            error=repr(err),
        )

    order_id = _extract_order_id(posted)
    if not order_id:
        # cannot poll without an id — treat as unsupported and let caller decide.
        return LimitExecutionResult(
            mode="limit_unsupported",
            filled=False,
            decision_price=float(decision_price),
            live_clob_price_before_order=float(live_clob_before),
            limit_price=float(limit_px),
            timeout_sec=int(timeout_sec),
            market_response=posted if isinstance(posted, dict) else {"raw": posted},
        )

    deadline = time.time() + max(1, int(timeout_sec))
    last_state: Dict[str, Any] = {}
    while time.time() < deadline:
        last_state = client.get_order_state(order_id) or {}
        if order_fully_filled(last_state):
            fill_px = _filled_price(last_state, fallback=float(limit_px))
            shares = _filled_shares(
                last_state, fallback=float(usd_amount) / max(limit_px, 1e-9)
            )
            slippage = max(0.0, fill_px - float(decision_price))
            return LimitExecutionResult(
                mode="limit_filled",
                filled=True,
                fill_price=fill_px,
                filled_shares=shares,
                order_id=order_id,
                decision_price=float(decision_price),
                live_clob_price_before_order=float(live_clob_before),
                limit_price=float(limit_px),
                timeout_sec=int(timeout_sec),
                slippage_estimate=slippage,
            )
        time.sleep(_LIMIT_POLL_INTERVAL_SEC)

    client.cancel_order(order_id)
    # refresh for telemetry: initial ask may have been 0 (book gap); unfilled often
    # means price moved away from our limit.
    live_at_end = _effective_best_ask_yes(client, market, float(decision_price))

    final_state: Dict[str, Any] = {}
    try:
        final_state = client.get_order_state(order_id) or {}
        matched = float(
            final_state.get("size_matched")
            or final_state.get("filled_size")
            or final_state.get("sizeMatched")
            or 0.0
        )
        if matched > 0:
            fill_px = _filled_price(final_state, fallback=float(limit_px))
            slippage = max(0.0, fill_px - float(decision_price))
            print(
                f"[limit_executor] late-fill detected after cancel "
                f"order={order_id} matched={matched:.4f} fill_px={fill_px:.4f}"
            )
            return LimitExecutionResult(
                mode="limit_filled_late",
                filled=True,
                fill_price=fill_px,
                filled_shares=matched,
                order_id=order_id,
                decision_price=float(decision_price),
                live_clob_price_before_order=float(live_at_end or live_clob_before),
                limit_price=float(limit_px),
                timeout_sec=int(timeout_sec),
                slippage_estimate=slippage,
            )
    except Exception:
        final_state = {}

    # cancel hardening: GTC can remain "live" if cancel races or the UI still shows OPEN.
    for _ in range(15):
        if not order_resting_unfilled(final_state):
            break
        client.cancel_order(order_id)
        time.sleep(0.22)
        try:
            final_state = client.get_order_state(order_id) or {}
        except Exception:
            final_state = {}

    try:
        matched2 = float(
            final_state.get("size_matched")
            or final_state.get("filled_size")
            or final_state.get("sizeMatched")
            or 0.0
        )
        if matched2 > 0:
            fill_px = _filled_price(final_state, fallback=float(limit_px))
            slippage = max(0.0, fill_px - float(decision_price))
            print(
                f"[limit_executor] late-fill during cancel-harden "
                f"order={order_id} matched={matched2:.4f} fill_px={fill_px:.4f}"
            )
            return LimitExecutionResult(
                mode="limit_filled_late",
                filled=True,
                fill_price=fill_px,
                filled_shares=matched2,
                order_id=order_id,
                decision_price=float(decision_price),
                live_clob_price_before_order=float(live_at_end or live_clob_before),
                limit_price=float(limit_px),
                timeout_sec=int(timeout_sec),
                slippage_estimate=slippage,
            )
    except Exception:
        pass

    stale = order_resting_unfilled(final_state)
    if stale:
        print(
            f"[limit_executor] WARNING: buy limit may still be OPEN on exchange "
            f"order_id={order_id} market={market.get('id')} — next place_buy will "
            f"retry cancel via orphan_limit_buy_orders"
        )

    return LimitExecutionResult(
        mode="limit_cancelled",
        filled=False,
        order_id=order_id,
        decision_price=float(decision_price),
        live_clob_price_before_order=float(live_at_end or live_clob_before),
        limit_price=float(limit_px),
        timeout_sec=int(timeout_sec),
        market_response=final_state,
        error=("open_order_may_remain_on_exchange" if stale else ""),
        stale_exchange_order=stale,
    )


def _extract_order_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    nested = payload.get("order")
    if isinstance(nested, dict):
        for key in ("id", "orderID", "orderId", "oid"):
            v = str(nested.get(key) or "").strip()
            if v:
                return v
    for key in ("orderID", "orderId", "id", "oid", "transactionHash"):
        v = str(payload.get(key) or "").strip()
        if v:
            return v
    return ""


def _filled_price(payload: Dict[str, Any], *, fallback: float) -> float:
    for key in ("filled_price", "avg_price", "average_price", "price"):
        try:
            v = float(payload.get(key) or 0.0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return float(fallback)


def _filled_shares(payload: Dict[str, Any], *, fallback: float) -> float:
    for key in ("filled_size", "size_matched", "sizeMatched", "size"):
        try:
            v = float(payload.get(key) or 0.0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return float(fallback)
