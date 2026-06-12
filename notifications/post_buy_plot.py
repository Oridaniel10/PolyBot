"""background PNG (event siblings) → Telegram sendPhoto after BUY."""

from __future__ import annotations

import os
import threading
import tempfile
from pathlib import Path
from typing import Any, Optional

from polymarket_client import PolymarketClient
from telegram_bot import tg_escape

from config.settings import get_effective_settings


def _thread_id_from_settings(st: Any) -> Optional[int]:
    tid = int(getattr(st, "telegram_message_thread_id", 0) or 0)
    if tid <= 0:
        tid = int(os.getenv("TELEGRAM_MESSAGE_THREAD_ID", "0") or "0")
    return tid if tid > 0 else None


def run_post_buy_event_chart_now(
    client: PolymarketClient,
    telegram: Any,
    market_id: str,
    *,
    context: str = "",
) -> None:
    """same work as the daemon thread — callable synchronously for tests."""
    mid = str(market_id or "").strip()
    if (
        not mid
        or not telegram
        or not getattr(telegram, "is_configured", lambda: False)()
    ):
        return
    try:
        st = get_effective_settings()
        if not bool(getattr(st, "post_buy_plot_enabled", True)):
            return
        wmin = float(getattr(st, "post_buy_plot_window_min", 15.0))
        wmin = max(1.0, min(120.0, wmin))
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        out = Path(tmp)
        from plot_recent_prices import render_post_buy_event_png

        ok = render_post_buy_event_png(mid, client, wmin, out)
        if not ok:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
            return
        tag = (context or "BUY").strip() or "BUY"
        cap = (
            f"📊 <b>Post-buy chart</b> · last {wmin:.0f}m · "
            f"{tg_escape(tag)}\n<code>{tg_escape(mid)}</code>"
        )
        mtid = _thread_id_from_settings(st)
        telegram.send_photo(str(out), caption=cap, message_thread_id=mtid)
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
    except Exception as exc:
        print(f"[post_buy_plot] failed: {exc!r}", flush=True)


def schedule_post_buy_event_chart(
    client: PolymarketClient,
    telegram: Any,
    market_id: str,
    *,
    context: str = "",
) -> None:
    """spawn daemon thread — never blocks place_buy / portfolio sync."""
    mid = str(market_id or "").strip()
    if not mid:
        return

    def _worker() -> None:
        run_post_buy_event_chart_now(client, telegram, mid, context=context)

    threading.Thread(
        target=_worker,
        name="post_buy_plot",
        daemon=True,
    ).start()
