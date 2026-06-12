import time as _time
from typing import Any, Dict, List, Set

from config.settings import (
    load_blacklist_day_ids,
    load_runtime_config_file,
    RuntimeSettings,
)
from strategy.time_utils import (
    format_countdown_seconds,
    seconds_until_end_of_report_day,
)


def runtime_blacklist_ids_only() -> Set[str]:
    """Blacklist ids from runtime_config.json only (before day-file merge)."""
    return set(
        RuntimeSettings.from_dict(load_runtime_config_file()).blacklist_market_ids
    )


def collect_blacklist_status_rows(
    state: Dict[str, Any],
    effective_blacklist_ids: Set[str],
) -> List[Dict[str, Any]]:
    """
    Build ordered rows for UI/telegram/terminal: churn cooldowns first, then
    other blacklisted markets with source (churn / day-ui / runtime) and remaining.
    """
    now = _time.time()
    churn = state.get("churn_by_market", {})
    active = state.get("active_trades", {})
    day_ids = load_blacklist_day_ids()
    cfg_ids = runtime_blacklist_ids_only()
    eod_sec = seconds_until_end_of_report_day()

    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for mid, c in churn.items():
        until = float(c.get("cooldown_until") or 0)
        if until <= now:
            continue
        rem = until - now
        title = str((active.get(mid) or {}).get("position_title") or mid)
        rows.append(
            {
                "market_id": mid,
                "title": title,
                "source": "churn",
                "remaining_sec": rem,
                "remaining_label": format_countdown_seconds(rem),
            }
        )
        seen.add(mid)

    for mid in sorted(effective_blacklist_ids):
        if mid in seen:
            continue
        title = str((active.get(mid) or {}).get("position_title") or mid)
        if mid in day_ids:
            src = "day"
            rem = eod_sec
            label = f"til midnight · {format_countdown_seconds(eod_sec)}"
        elif mid in cfg_ids:
            src = "runtime"
            rem = None
            label = "manual (runtime config)"
        else:
            src = "day"
            rem = eod_sec
            label = f"til midnight · {format_countdown_seconds(eod_sec)}"
        rows.append(
            {
                "market_id": mid,
                "title": title,
                "source": src,
                "remaining_sec": rem,
                "remaining_label": label,
            }
        )

    return rows
