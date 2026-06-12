"""Telegram dedup state for failed exit notifications.

Keeps a process-local map of (market_id, exit_reason) -> last_notice_ts so a
stop-loss/take-profit/emergency-exit sell that keeps failing every tick does
not flood the user's Telegram with the same alert. The first failure is sent;
repeats inside `telegram_failed_exit_cooldown_sec` are suppressed and only
written to the terminal log.

State lives in module memory because failed-exit retries happen in the same
bot process (main loop + fast watcher daemon). Restarts reset the dedup map
so a fresh process always re-notifies.
"""

from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

from config.settings import RuntimeSettings


_NoticeKey = Tuple[str, str, str]
# (market_id, exit_reason, error_category) -> last_notice_ts
_failed_exit_notices: dict[_NoticeKey, float] = {}
_lock = threading.Lock()


def _normalize(value: str) -> str:
    return str(value or "").strip()


def _build_key(market_id: str, exit_reason: str, error_category: str) -> _NoticeKey:
    return (
        _normalize(market_id),
        _normalize(exit_reason),
        _normalize(error_category),
    )


def categorize_error(detail: str) -> str:
    """coarse fingerprint of the error (not full stack trace).

    keeps separate failure modes distinct without spamming on every tiny
    request-id change. used as the third dedup key component.
    """
    s = _normalize(detail).lower()
    if not s:
        return "unknown"
    if "below_min_order_size" in s or "below min order size" in s:
        return "below_min"
    if "insufficient" in s and ("balance" in s or "allowance" in s):
        return "insufficient_balance"
    if "rate limit" in s or "too many requests" in s or "429" in s:
        return "rate_limited"
    if "timeout" in s or "timed out" in s:
        return "timeout"
    if "version_mismatch" in s or "order_version" in s:
        return "version_mismatch"
    if "404" in s:
        return "not_found"
    if "5" in s[:3] and "0" in s[:3]:  # 5xx in payload prefix
        return "server_error"
    return "generic"


def should_send_failed_exit_notice(
    settings: RuntimeSettings,
    *,
    market_id: str,
    exit_reason: str,
    error_category: str,
) -> bool:
    """returns True if a fresh telegram notice should be sent for this failure.

    side effect: when returning True, records the timestamp so further calls
    inside the cooldown window suppress.
    """
    if not bool(getattr(settings, "telegram_failed_exit_dedupe_enabled", True)):
        return True
    cooldown = float(getattr(settings, "telegram_failed_exit_cooldown_sec", 900))
    if cooldown <= 0:
        return True
    key = _build_key(market_id, exit_reason, error_category)
    now = time.time()
    with _lock:
        last = _failed_exit_notices.get(key, 0.0)
        if now - last < cooldown:
            return False
        _failed_exit_notices[key] = now
    return True


def clear_failed_exit_notices(market_id: str) -> None:
    """drop all dedup entries for a market once the sell finally succeeds.

    next failure on the same market starts fresh and notifies once again.
    """
    mid = _normalize(market_id)
    if not mid:
        return
    with _lock:
        for k in list(_failed_exit_notices.keys()):
            if k[0] == mid:
                _failed_exit_notices.pop(k, None)


def peek_last_notice_age(
    market_id: str, exit_reason: str, error_category: str
) -> Optional[float]:
    """seconds since the last notice for this combination, or None if never sent."""
    key = _build_key(market_id, exit_reason, error_category)
    with _lock:
        last = _failed_exit_notices.get(key)
    if last is None:
        return None
    return max(0.0, time.time() - last)
