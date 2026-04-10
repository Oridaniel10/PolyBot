import time
from typing import Any, Dict


def churn_allows_buy(state: Dict[str, Any], market_id: str) -> bool:
    ch = state.setdefault("churn_by_market", {})
    c = ch.get(market_id) or {}
    now = time.time()
    until = float(c.get("cooldown_until") or 0)
    if until > 0 and now >= until:
        c["stop_cycles"] = 0
        c["cooldown_until"] = 0.0
        ch[market_id] = c
    if now < float(c.get("cooldown_until") or 0):
        return False
    return True


def churn_on_stop_loss_exit(
    state: Dict[str, Any], market_id: str, max_cycles: int, cooldown_sec: int
) -> None:
    ch = state.setdefault("churn_by_market", {})
    c = dict(ch.get(market_id) or {"stop_cycles": 0, "cooldown_until": 0.0})
    c["stop_cycles"] = int(c.get("stop_cycles") or 0) + 1
    if c["stop_cycles"] >= max_cycles:
        c["cooldown_until"] = time.time() + float(cooldown_sec)
    ch[market_id] = c


def churn_on_take_profit(state: Dict[str, Any], market_id: str) -> None:
    ch = state.setdefault("churn_by_market", {})
    ch[market_id] = {"stop_cycles": 0, "cooldown_until": 0.0}
