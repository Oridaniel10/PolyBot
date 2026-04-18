"""Time-based entry and exit filters.

- Entry: city local calendar date must match the market event_date, and hour 14:00-24:00
- Exit: time-decay rule — if held >2h, <5% gain, price <85% → exit
"""

import time
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from strategy.city_tz import city_local_hour, city_local_now

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]


def entry_time_allowed(
    title: str,
    earliest_hour: int = 14,
    latest_hour: int = 24,
    event_date: Optional[date] = None,
) -> Tuple[bool, Optional[int], str]:
    """
    check if the city's local clock is on the event day and within the buy hour window.

    returns:
    - (allowed, city_hour_or_none, skip_reason empty when allowed).
    """
    hour = city_local_hour(title)
    if hour is None:
        return True, None, ""
    if hour < earliest_hour:
        return False, hour, "before_earliest_hour"
    if latest_hour < 24 and hour > latest_hour:
        return False, hour, "after_latest_hour"
    if event_date is not None:
        now_city = city_local_now(title)
        if now_city is None:
            return False, hour, "no_city_datetime"
        if now_city.date() != event_date:
            return False, hour, "city_local_date_not_event_day"
    return True, hour, ""


def should_time_decay_exit(
    trade: Dict[str, Any],
    current_price: float,
    decay_hours: float = 2.0,
    min_gain_pct: float = 0.05,
    max_price_for_decay: float = 0.85,
    now_ts: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    exit if position held too long without meaningful price increase.

    conditions (ALL must be true):
    - held > decay_hours
    - price hasn't risen by min_gain_pct from entry
    - current price < max_price_for_decay

    returns:
    - (should_exit, reason_string).
    """
    entry_time = trade.get("entry_time_utc")
    if not entry_time:
        return False, ""

    now_ts = now_ts or time.time()
    try:
        if isinstance(entry_time, str):
            if entry_time.endswith("Z"):
                entry_time = entry_time[:-1] + "+00:00"
            et = datetime.fromisoformat(entry_time).timestamp()
        else:
            et = float(entry_time)
    except (ValueError, TypeError):
        return False, ""

    hours_held = (now_ts - et) / 3600.0
    if hours_held < decay_hours:
        return False, ""

    entry_price = float(trade.get("entry_price") or 0)
    if entry_price <= 1e-9:
        return False, ""

    gain_pct = (current_price - entry_price) / entry_price
    if gain_pct >= min_gain_pct:
        return False, ""

    if current_price >= max_price_for_decay:
        return False, ""

    reason = (
        f"time_decay held={hours_held:.1f}h gain={gain_pct:+.1%} "
        f"price={current_price:.4f} < {max_price_for_decay}"
    )
    return True, reason
