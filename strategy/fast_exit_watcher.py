"""Fast exit watcher — daemon thread polling CLOB price every ~2 seconds.

Monitors open positions for:
1. Stop-loss (per-type SL bar: mark < SL threshold)
2. Momentum fast exit (absolute price drop from peak in 15m window)

Uses CLOB /book endpoint directly (live best-ask / midpoint, NOT Gamma bestAsk
which can be stale). At default 2s interval and ~7 positions, this is
~35 req/10s — well within CLOB rate limits.
"""

import json
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# ensure repo root is on sys.path (same pattern as bot_runner.py)
if not __package__:
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import config.constants as C
from config.settings import RuntimeSettings
from polymarket_client import PolymarketClient
from strategy.decision_core import (
    effective_stop_price_for_trade,
    momentum_fast_exit_drop,
    momentum_window_sec,
)
from strategy.probability import stop_loss_reference_if_triggered
from telegram_bot import TelegramBot


def _log(msg: str) -> None:
    print(f"[fast_exit_watcher] {msg}")


class FastExitWatcher:
    """Daemon thread that polls CLOB price for open positions and triggers emergency exits."""

    def __init__(
        self,
        client: PolymarketClient,
        state_ref: Dict[str, Any],
        telegram: TelegramBot,
        get_settings_fn: Callable[[], RuntimeSettings],
        *,
        save_state_fn: Optional[Callable[[], None]] = None,
    ):
        self._client = client
        self._state = state_ref
        self._telegram = telegram
        self._get_settings = get_settings_fn
        self._save_state = save_state_fn
        self._sell_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="fast_exit_watcher"
        )
        self._thread.start()
        _log("started")

    def stop(self) -> None:
        self._stop_event.set()
        _log("stop requested")

    @property
    def sell_lock(self) -> threading.Lock:
        """Expose lock so main loop can use it to prevent double-sells."""
        return self._sell_lock

    def _run_loop(self) -> None:
        tick = 0
        while not self._stop_event.is_set():
            try:
                settings = self._get_settings()
                interval = max(2, int(
                    getattr(settings, "fast_exit_watcher_interval_sec", C.FAST_EXIT_WATCHER_INTERVAL_SEC)
                ))
            except Exception:
                interval = C.FAST_EXIT_WATCHER_INTERVAL_SEC
            try:
                n_pos = self._poll_once()
                tick += 1
                # heartbeat every ~60 seconds so user sees it's alive
                if tick % 12 == 0:
                    _log(f"♥ alive — watching {n_pos} position(s), tick #{tick}")
            except Exception as exc:
                _log(f"poll error: {exc!r}")
                traceback.print_exc()
            self._stop_event.wait(timeout=interval)
        _log("stopped")

    def _poll_once(self) -> int:
        active = self._state.get("active_trades")
        if not active or not isinstance(active, dict):
            return 0
        settings = self._get_settings()
        # snapshot keys to avoid mutation during iteration
        items = list(active.items())
        checked = 0
        for market_id, trade_row in items:
            if not isinstance(trade_row, dict):
                continue
            if self._stop_event.is_set():
                break
            try:
                self._check_position(market_id, trade_row, settings)
                checked += 1
            except Exception as exc:
                _log(f"check {market_id} error: {exc!r}")
        return checked

    def _check_position(
        self,
        market_id: str,
        trade_row: Dict[str, Any],
        settings: RuntimeSettings,
    ) -> None:
        entry = float(trade_row.get("entry_price") or 0)
        if entry <= 1e-9:
            return

        # always-fresh CLOB price (best ask / midpoint) — bypasses Gamma bestAsk
        # which can be stale by tens of seconds and miss real price drops.
        try:
            clob_price = self._client.get_clob_yes_price_live_by_id(market_id)
        except Exception:
            return
        if clob_price <= 0:
            return

        # update trade row mark so the slow main loop sees fresh price too
        trade_row["last_price"] = clob_price

        # --- check 1: per-type stop-loss ---
        entry_type = str(trade_row.get("entry_type") or "normal").strip()
        sl_bar = effective_stop_price_for_trade(entry_type, entry, settings)
        _log(
            f"watch market={market_id} live={clob_price:.4f} entry={entry:.4f} "
            f"effective_sl={sl_bar:.4f} type={entry_type}"
        )
        if clob_price < sl_bar and clob_price < entry - 1e-9:
            _log(
                f"SL trigger market={market_id} clob={clob_price:.4f} "
                f"sl_bar={sl_bar:.4f} entry={entry:.4f} type={entry_type}"
            )
            self._trigger_exit(market_id, trade_row, clob_price, "stop-loss", settings)
            return

        # --- check 2: momentum fast exit (absolute drop in 15m window) ---
        from strategy.momentum_engine import max_drawdown_since_ts
        wsec = momentum_window_sec(settings)
        dd = 0.0
        entry_time_utc = str(trade_row.get("entry_time_utc") or "").strip()
        if entry_time_utc:
            try:
                entry_ts = datetime.fromisoformat(entry_time_utc).timestamp()
            except ValueError:
                entry_ts = 0.0
            if entry_ts > 0:
                dd = max_drawdown_since_ts(market_id, entry_ts, wsec)
        if dd >= momentum_fast_exit_drop(settings) and clob_price < entry:
            _log(
                f"momentum-SL trigger market={market_id} dd={dd:.4f} "
                f"clob={clob_price:.4f} entry={entry:.4f}"
            )
            self._trigger_exit(
                market_id, trade_row, clob_price, "momentum-stop-loss", settings
            )
            return
        _log(
            f"hold market={market_id} no-trigger dd={dd:.4f} "
            f"need_dd={momentum_fast_exit_drop(settings):.4f}"
        )

    def _trigger_exit(
        self,
        market_id: str,
        trade_row: Dict[str, Any],
        price: float,
        reason: str,
        settings: RuntimeSettings,
    ) -> None:
        """Execute an emergency sell, guarded by lock to prevent double-sells."""
        if not self._sell_lock.acquire(blocking=False):
            _log(f"sell_lock busy, skipping {reason} for {market_id}")
            return
        try:
            # re-check position still exists (main loop may have sold it)
            active = self._state.get("active_trades") or {}
            if market_id not in active:
                return

            title = str(trade_row.get("position_title") or "")
            entry = float(trade_row.get("entry_price") or 0)
            _log(
                f"🚨 FAST EXIT: {reason} market={market_id} "
                f"price={price:.4f} entry={entry:.4f} title={title[:80]}"
            )

            # resolve gamma market for close_position
            market = self._client.get_market_by_id(market_id)
            if not market:
                _log(f"cannot resolve market {market_id} — skipping exit")
                return

            from strategy.trades import close_position
            close_position(
                self._client,
                market,
                self._state,
                self._telegram,
                price,
                reason,
                settings,
                state_trade_key=market_id,
            )
            if self._save_state:
                try:
                    self._save_state()
                except Exception:
                    pass
        finally:
            self._sell_lock.release()


if __name__ == "__main__":
    from config.settings import get_effective_settings

    s = get_effective_settings()
    print("═" * 60)
    print("  Fast Exit Watcher — module health check")
    print("═" * 60)
    print(f"  interval:                {s.fast_exit_watcher_interval_sec}s")
    print(
        "  momentum_fast_exit_drop: "
        f"{momentum_fast_exit_drop(s)} (absolute, from runtime)"
    )
    print(f"  momentum_window:         {momentum_window_sec(s)}s (from runtime)")
    print(f"  SL normal:               {s.stop_loss_normal}")
    print(f"  SL momentum:             {s.stop_loss_momentum}")
    print(f"  SL double_momentum:      {s.stop_loss_double_momentum}")
    print("─" * 60)
    print("  ✅ Module loads OK.")
    print("  ℹ️  This runs automatically as a daemon thread inside bot_runner.py.")
    print("  ℹ️  To start the bot: python strategy/bot_runner.py")
    print("═" * 60)
