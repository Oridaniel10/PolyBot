"""Time-based entry and exit filters.

- Entry: city local calendar date must match the market event_date, and hour 14:00-24:00
- Exit: time-decay — if held >N h, gain vs entry < min_gain (absolute price points),
  and mark < max_price → exit
"""

import time
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from strategy.city_tz import city_local_now

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]


def entry_time_allowed(
    title: str,
    earliest_hour: float = 14.0,
    latest_hour: float = 24.0,
    event_date: Optional[date] = None,
) -> Tuple[bool, Optional[float], str]:
    """Check if the city's local clock is within the buy hour window.

    earliest_hour / latest_hour are floats: 15.5 = 15:30, 15.25 = 15:15.

    Returns:
        (allowed, city_hour_float_or_none, skip_reason_when_blocked)
        city_hour_float is e.g. 15.5 when the city clock is 15:30:00.
    """
    now_city = city_local_now(title)
    if now_city is None:
        return True, None, ""
    # convert local time to fractional hour (minute granularity, drop seconds)
    city_hour_float = float(now_city.hour) + float(now_city.minute) / 60.0
    if city_hour_float + 1e-9 < float(earliest_hour):
        return False, city_hour_float, "before_earliest_hour"
    if float(latest_hour) < 24.0 and city_hour_float - 1e-9 > float(latest_hour):
        return False, city_hour_float, "after_latest_hour"
    if event_date is not None:
        if now_city.date() != event_date:
            return False, city_hour_float, "city_local_date_not_event_day"
    return True, city_hour_float, ""


def should_time_decay_exit(
    trade: Dict[str, Any],
    current_price: float,
    decay_hours: float = 2.0,
    min_gain_points: float = 0.02,
    max_price_for_decay: float = 0.85,
    now_ts: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    exit if position held too long without meaningful price increase.

    conditions (ALL must be true):
    - held > decay_hours
    - (current_price - entry_price) < min_gain_points (absolute YES points)
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

    gain_pts = float(current_price) - float(entry_price)
    if gain_pts + 1e-12 >= float(min_gain_points):
        return False, ""

    if current_price >= max_price_for_decay:
        return False, ""

    reason = (
        f"time_decay held={hours_held:.1f}h gain_pts={gain_pts:+.4f} "
        f"< min_pts={float(min_gain_points):.4f} "
        f"price={current_price:.4f} < {max_price_for_decay}"
    )
    return True, reason
