from datetime import datetime, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]

from config.constants import TIMEZONE


def now_in_report_timezone() -> datetime:
    if ZoneInfo is None:
        return datetime.utcnow()
    return datetime.now(ZoneInfo(TIMEZONE))


def build_target_day_label(now: datetime) -> str:
    return f"{now.strftime('%B')} {now.day}"


def format_report_local_hhmm(now: Optional[datetime] = None) -> str:
    """Return local clock time in report timezone as HH:MM (24h)."""
    dt = now or now_in_report_timezone()
    return dt.strftime("%H:%M")


def seconds_until_end_of_report_day(now: Optional[datetime] = None) -> float:
    """Seconds until midnight in report timezone (Asia/Jerusalem by default)."""
    dt = now or now_in_report_timezone()
    if ZoneInfo is None:
        return 0.0
    nxt = (dt + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(0.0, (nxt - dt).total_seconds())


def format_countdown_seconds(seconds: float) -> str:
    """Human countdown: minutes/seconds or hours+minutes."""
    if seconds <= 0:
        return "0m"
    if seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        if m <= 0:
            return f"{s}s"
        return f"{m}m {s}s" if s else f"{m}m"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m" if m else f"{h}h"
