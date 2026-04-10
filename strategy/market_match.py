from typing import Any, Dict, Optional, Tuple

import requests

from polymarket_client import (
    condition_ids_equivalent,
    gamma_market_condition_id,
    gamma_market_matches_condition,
)
from notifications.terminal import term_wrap
from telegram_bot import TelegramBot, tg_escape

from config.constants import TERM_RED


def active_trade_key_and_row(
    active_trades: Dict[str, Any], market: Dict[str, Any]
) -> Tuple[str, Optional[Dict[str, Any]]]:
    mid = str(market.get("id") or "").strip()
    if mid and mid in active_trades:
        return mid, active_trades[mid]
    mcid = str(market.get("conditionId") or market.get("condition_id") or "").strip()
    if not mcid:
        return "", None
    for tid, tr in active_trades.items():
        tc = str(tr.get("condition_id") or "")
        if tc and condition_ids_equivalent(mcid, tc):
            return str(tid), tr
    return "", None


def trade_position_title_matches_gamma_market(
    trade: Optional[Dict[str, Any]], market: Dict[str, Any]
) -> bool:
    if not trade:
        return True
    pt = str(trade.get("position_title") or "").strip().lower()
    if not pt:
        return True
    mq = str(market.get("question") or market.get("title") or "").strip().lower()
    if not mq:
        return False
    if pt == mq:
        return True
    return pt in mq or mq in pt


def trade_row_matches_gamma_market(
    trade: Optional[Dict[str, Any]], market: Dict[str, Any]
) -> bool:
    if not trade:
        return True
    cid = str(trade.get("condition_id") or "").strip()
    mid_m = str(market.get("id") or "").strip()
    mid_t = str(trade.get("market_id") or "").strip()
    if cid:
        gc = gamma_market_condition_id(market)
        if gc:
            if gamma_market_matches_condition(market, cid):
                return True
            return False
        if mid_t and mid_m and mid_t != mid_m:
            return False
    return trade_position_title_matches_gamma_market(trade, market)


def warn_gamma_trade_mismatch_once(
    trade: Dict[str, Any], market: Dict[str, Any], telegram: TelegramBot
) -> None:
    if trade.get("_warned_gamma_trade_mismatch"):
        return
    trade["_warned_gamma_trade_mismatch"] = True
    mq = market.get("question") or market.get("title") or market.get("id")
    pt = trade.get("position_title") or trade.get("condition_id") or "?"
    msg = (
        "SKIP TRADE: gamma market does not match data-api row "
        "(wrong-token sell avoided). check condition_id / title.\n"
        f"portfolio_title~={pt!r}\n"
        f"gamma_question~={mq!r}"
    )
    print(term_wrap(TERM_RED, msg))
    try:
        if telegram.is_configured():
            telegram.send_html_chunks(
                f"⚠️ <b>Gamma / portfolio mismatch</b>\n"
                f"<b>data-api</b>\n{tg_escape(pt)}\n"
                f"<b>gamma</b>\n{tg_escape(mq)}"
            )
    except requests.RequestException:
        pass
