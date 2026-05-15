"""Momentum debug alert — sends Telegram + event plot when any sibling moves significantly.

Controlled by constants (or runtime_config.json overrides):
  MOMENTUM_ALERT_ENABLED      master on/off
  MOMENTUM_ALERT_MIN_ABS      min absolute price move (e.g. 0.10)
  MOMENTUM_ALERT_MIN_PCT      min fractional move (e.g. 0.30 = 30%)
  MOMENTUM_ALERT_WINDOW_SEC   lookback window in seconds (900 = 15min, 7200 = 2h)
  MOMENTUM_ALERT_COOLDOWN_SEC min seconds before re-alerting the same market
"""

import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import config.constants as C
from strategy.momentum_engine import price_change_in_window


# in-memory cooldown: {market_id: last_alert_ts}
_last_alert: Dict[str, float] = {}


def _settings_val(settings: Any, attr: str, default: float) -> float:
    try:
        v = getattr(settings, attr, None)
        if v is None:
            v = default
        return float(v)
    except (TypeError, ValueError):
        return default


def check_and_alert(
    event_cache: Dict[str, List[Dict[str, Any]]],
    telegram: Any,
    client: Any,
    settings: Any,
) -> None:
    """Check every cached sibling for large moves; alert + plot on hit.

    Designed to run in a daemon thread — never raises, all errors are swallowed.
    """
    try:
        enabled = bool(getattr(settings, "momentum_alert_enabled", C.MOMENTUM_ALERT_ENABLED))
        min_abs = _settings_val(settings, "momentum_alert_min_abs", C.MOMENTUM_ALERT_MIN_ABS)
        min_pct = _settings_val(settings, "momentum_alert_min_pct", C.MOMENTUM_ALERT_MIN_PCT)
        window_sec = _settings_val(settings, "momentum_alert_window_sec", C.MOMENTUM_ALERT_WINDOW_SEC)
        cooldown = _settings_val(settings, "momentum_alert_cooldown_sec", C.MOMENTUM_ALERT_COOLDOWN_SEC)

        print(
            f"[momentum_alert] tick: enabled={enabled} events={len(event_cache)} "
            f"min_abs={min_abs} min_pct={min_pct} window={window_sec/60:.0f}m "
            f"tg={telegram.is_configured()}"
        )

        if not enabled:
            return
        if not telegram.is_configured():
            return

        now = time.time()
        alerted_events: Set[str] = set()

        for event_id, siblings in event_cache.items():
            if not siblings:
                continue
            for m in siblings:
                if not isinstance(m, dict):
                    continue
                mid = str(m.get("id") or "").strip()
                if not mid:
                    continue

                # cooldown check
                last = _last_alert.get(mid, 0.0)
                if now - last < cooldown:
                    continue

                old_p, new_p, pct_change = price_change_in_window(mid, window_sec, now)
                if old_p <= 1e-9 or new_p <= 1e-9:
                    continue

                abs_change = abs(new_p - old_p)
                abs_pct = abs(pct_change)
                print(
                    f"[momentum_alert] check mid={mid} old={old_p:.4f} new={new_p:.4f} "
                    f"abs={abs_change:.4f} pct={abs_pct:.2%} "
                    f"(need abs>={min_abs} OR pct>={min_pct:.0%})"
                )

                if abs_change < min_abs - 1e-9 and abs_pct < min_pct - 1e-9:
                    continue

                # threshold hit — update cooldown and alert once per event per cycle
                _last_alert[mid] = now
                direction = "▲ UP" if new_p > old_p else "▼ DOWN"
                title = str(m.get("question") or m.get("title") or mid)[:80]
                win_min = int(window_sec / 60)

                print(
                    f"[momentum_alert] {direction} market={mid} "
                    f"old={old_p:.4f} new={new_p:.4f} "
                    f"abs={abs_change:+.4f} pct={pct_change:+.1%} "
                    f"window={win_min}m\n  {title}"
                )

                # send text alert
                msg = (
                    f"📊 <b>Momentum Alert</b> {direction}\n"
                    f"<b>{title}</b>\n"
                    f"Window: {win_min}m  |  "
                    f"Old: {old_p:.3f}  →  New: {new_p:.3f}\n"
                    f"Move: <b>{new_p - old_p:+.4f} pts ({pct_change:+.1%})</b>"
                )
                try:
                    telegram.send_html_chunks(msg)
                except Exception as exc:
                    print(f"[momentum_alert] telegram text failed: {exc!r}")

                # send event plot (once per event per cycle)
                if event_id not in alerted_events:
                    alerted_events.add(event_id)
                    _send_event_plot(event_id, mid, client, telegram, window_sec, now)

    except Exception as exc:
        print(f"[momentum_alert] error: {exc!r}")


def _send_event_plot(
    event_id: str,
    market_id: str,
    client: Any,
    telegram: Any,
    window_sec: float,
    now_ts: float,
) -> None:
    try:
        from plot_recent_prices import render_post_buy_event_png

        win_min = window_sec / 60.0
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / f"alert_{event_id}.png"
            ok = render_post_buy_event_png(market_id, client, win_min, out_path, now_ts=now_ts)
            if ok and out_path.is_file():
                caption = f"Event {event_id} · last {int(win_min)}m"
                telegram.send_photo(str(out_path), caption=caption)
    except Exception as exc:
        print(f"[momentum_alert] plot/send failed: {exc!r}")
