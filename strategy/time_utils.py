from datetime import datetime

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
