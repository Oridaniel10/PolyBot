"""Anti-churn guards — per-market and per-event cooldowns after stop-loss exits.

Per-market: block re-buying the same market_id after N stop-losses.
Per-event: block buying ANY market in the same gamma event after N losses
across any sibling markets (prevents switching from Paris 22°C to Paris 23°C
and losing again).
"""

import time
from typing import Any, Dict


# ─── Per-market churn ─────────────────────────────────────────────────

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


# ─── Per-event churn ──────────────────────────────────────────────────

def churn_event_allows_buy(state: Dict[str, Any], event_id: str) -> bool:
    """Check if event-level churn cooldown allows a new buy for this event."""
    if not event_id:
        return True
    ch = state.setdefault("churn_by_event", {})
    c = ch.get(event_id) or {}
    now = time.time()
    until = float(c.get("cooldown_until") or 0)
    if until > 0 and now >= until:
        # cooldown expired — reset
        c["loss_count"] = 0
        c["cooldown_until"] = 0.0
        ch[event_id] = c
    if now < float(c.get("cooldown_until") or 0):
        return False
    return True


def churn_on_event_loss(
    state: Dict[str, Any],
    event_id: str,
    max_losses: int,
    cooldown_sec: int,
) -> None:
    """Increment event-level loss counter; set cooldown if threshold reached."""
    if not event_id or cooldown_sec <= 0:
        return
    ch = state.setdefault("churn_by_event", {})
    c = dict(ch.get(event_id) or {"loss_count": 0, "cooldown_until": 0.0})
    c["loss_count"] = int(c.get("loss_count") or 0) + 1
    if c["loss_count"] >= max_losses:
        c["cooldown_until"] = time.time() + float(cooldown_sec)
    ch[event_id] = c


def churn_event_mark_loss_tiered(
    state: Dict[str, Any],
    event_id: str,
    cooldown_loss_1_sec: int,
    cooldown_loss_2_sec: int,
) -> None:
    """Apply tiered event cooldown after losses."""
    if not event_id:
        return
    ch = state.setdefault("churn_by_event", {})
    c = dict(ch.get(event_id) or {"loss_count": 0, "cooldown_until": 0.0})
    c["loss_count"] = int(c.get("loss_count") or 0) + 1
    losses = int(c["loss_count"])
    cooldown = 0
    if losses >= 2:
        cooldown = int(cooldown_loss_2_sec)
    elif losses >= 1:
        cooldown = int(cooldown_loss_1_sec)
    if cooldown > 0:
        c["cooldown_until"] = time.time() + float(cooldown)
    ch[event_id] = c


def churn_mark_event_recent_stoploss(
    state: Dict[str, Any], event_id: str, cooldown_sec: int
) -> None:
    if not event_id or cooldown_sec <= 0:
        return
    root = state.setdefault("event_recent_stoploss", {})
    if not isinstance(root, dict):
        root = {}
        state["event_recent_stoploss"] = root
    root[event_id] = time.time() + float(cooldown_sec)


def churn_event_recent_stoploss_active(state: Dict[str, Any], event_id: str) -> bool:
    if not event_id:
        return False
    root = state.setdefault("event_recent_stoploss", {})
    if not isinstance(root, dict):
        return False
    now = time.time()
    until = float(root.get(event_id) or 0.0)
    if until <= 0 or now >= until:
        root.pop(event_id, None)
        return False
    return True


def churn_record_event_leader_switch(
    state: Dict[str, Any],
    event_id: str,
    now_ts: float,
    window_sec: int,
    max_count: int,
    unstable_cooldown_sec: int,
) -> bool:
    """Record leader switch timestamps; return True when event becomes unstable."""
    if not event_id:
        return False
    root = state.setdefault("event_leader_switch", {})
    if not isinstance(root, dict):
        root = {}
        state["event_leader_switch"] = root
    row = root.setdefault(event_id, {})
    if not isinstance(row, dict):
        row = {}
        root[event_id] = row
    stamps = row.get("switch_ts")
    if not isinstance(stamps, list):
        stamps = []
    min_ts = float(now_ts) - max(1, int(window_sec))
    trimmed = [float(x) for x in stamps if float(x) >= min_ts]
    trimmed.append(float(now_ts))
    row["switch_ts"] = trimmed
    if len(trimmed) > max(1, int(max_count)):
        until = float(now_ts) + max(0, int(unstable_cooldown_sec))
        row["unstable_until"] = until
        return True
    return False


def churn_event_unstable_active(state: Dict[str, Any], event_id: str) -> bool:
    if not event_id:
        return False
    root = state.setdefault("event_leader_switch", {})
    if not isinstance(root, dict):
        return False
    row = root.get(event_id) or {}
    if not isinstance(row, dict):
        return False
    now = time.time()
    until = float(row.get("unstable_until") or 0.0)
    if until <= 0:
        return False
    if now >= until:
        row["unstable_until"] = 0.0
        row["switch_ts"] = []
        root[event_id] = row
        return False
    return True
