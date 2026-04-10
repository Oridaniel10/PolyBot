from typing import Any, Dict

from config.constants import STATUS_CLOSED


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
