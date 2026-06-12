"""Startup-only digest: effective runtime + key constants (see STRATEGY_LOGIC.md)."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from config import constants as C
from config.settings import RuntimeSettings
from telegram_bot import tg_escape


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        s = f"{v:.6g}"
        if "e" in s.lower():
            return f"{v:.4f}"
        return s
    return str(v)


def _digest_rows(
    settings: RuntimeSettings,
) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """(title_or_key, value_str or None for section header, meaning or None)."""
    r: List[Tuple[str, Optional[str], Optional[str]]] = []

    def sec(title: str) -> None:
        r.append((title, None, None))

    def row(key: str, val: Any, meaning: str) -> None:
        r.append((key, _fmt(val), meaning))

    sec("Buy band & model/market gates")
    row(
        "buy_min_threshold",
        settings.buy_min,
        "min YES for normal entry; also floor for momentum entry",
    )
    row(
        "buy_max_threshold",
        settings.buy_max,
        "max YES for buys (with CLOB band in trades)",
    )
    row(
        "buy_disable_price_band",
        settings.buy_disable_price_band,
        "if true, skip min/max band in buy path",
    )
    row(
        "max_market_prob_for_buy",
        settings.max_market_prob_for_buy,
        "skip buy if market YES above this",
    )
    row(
        "min_model_prob_for_buy",
        settings.min_model_prob_for_buy,
        "skip buy if Gaussian model P(YES) below this",
    )
    row(
        "decision_min_model_peak_prob",
        settings.decision_min_model_peak_prob,
        "skip if model peak prob too flat",
    )
    row(
        "max_positions_per_event",
        settings.max_positions_per_event,
        "max open positions per gamma event",
    )

    sec("Sizing & loop")
    row(
        "scan_interval_seconds",
        settings.scan_interval_seconds,
        "seconds between bot ticks",
    )
    row(
        "cash_reserve_usd",
        settings.cash_reserve_usd,
        "cash never allocated to new buys",
    )
    row("max_buy_notional_usd", settings.max_buy_notional_usd, "cap per buy ($)")
    row(
        "min_order_notional_usd",
        settings.min_order_notional_usd,
        "skip buy if planned below this",
    )
    row(
        "max_trade_fraction_of_cash",
        settings.max_trade_fraction_of_cash,
        "max fraction of free cash per buy",
    )

    sec("Stop-loss & take-profit")
    row("stop_loss_normal", settings.stop_loss_normal, "flat SL mark for normal entry")
    row(
        "stop_loss_momentum",
        settings.stop_loss_momentum,
        "flat SL mark for momentum entry",
    )
    row(
        "stop_loss_double_momentum",
        settings.stop_loss_double_momentum,
        "flat SL mark for double momentum entry",
    )
    row(
        "take_profit_threshold",
        settings.take_profit,
        "exit when mark at/above this (minus slack)",
    )

    sec("Competition")
    row(
        "enable_competition_filter",
        settings.enable_competition_filter,
        "require #1 bucket lead over #2",
    )
    row(
        "min_lead_over_runner_up",
        settings.min_lead_over_runner_up,
        "passed to evaluate_competition() for metadata / model branch",
    )
    row(
        "min_market_yes_lead_gap_normal",
        settings.min_market_yes_lead_gap_normal,
        "normal BUY only: min YES lead vs runner-up (momentum skips)",
    )

    sec("Churn / blacklist")
    row(
        "churn_max_stop_cycles",
        settings.churn_max_stop_cycles,
        "stop-loss exits before day blacklist",
    )
    row(
        "churn_cooldown_sec",
        settings.churn_cooldown_sec,
        "seconds market blacklisted after stop-like exit",
    )
    row(
        "blacklist_market_ids",
        len(settings.blacklist_market_ids),
        "count of ids in runtime blacklist set",
    )

    sec("Peer YES surge (siblings)")
    row(
        "enable_peer_surge_exit",
        settings.enable_peer_surge_exit,
        "exit when sibling YES surges in window",
    )
    row(
        "peer_surge_window_min",
        settings.peer_surge_window_min,
        "rolling window minutes",
    )
    row(
        "peer_surge_rise_threshold",
        settings.peer_surge_rise_threshold,
        "min rise ratio to trigger",
    )
    row(
        "peer_surge_event_cooldown_sec",
        settings.peer_surge_event_cooldown_sec,
        "cooldown after peer surge handling",
    )
    row(
        "peer_surge_skip_buy_enabled",
        settings.peer_surge_skip_buy_enabled,
        "skip buys on peer heat",
    )
    row(
        "peer_surge_skip_buy_rise_threshold",
        settings.peer_surge_skip_buy_rise_threshold,
        "rise threshold for skip-buy",
    )

    sec("Buy local time (city TZ)")
    row(
        "buy_earliest_local_hour",
        settings.buy_earliest_local_hour,
        "first local hour (city) allowed for new buys",
    )
    row(
        "buy_latest_local_hour",
        settings.buy_latest_local_hour,
        "no buys after this local hour (24 = off)",
    )

    sec("Telegram / advisor")
    row(
        "decision_skip_telegram_cooldown_sec",
        settings.decision_skip_telegram_cooldown_sec,
        "dedupe decision BUY skip spam",
    )
    row(
        "advisor_context_MODEL_PROB_MD",
        "MODEL_PROBABILITY_AND_CALIBRATION.md",
        "OpenRouter /ask bundles this file (optional context)",
    )
    row(
        "decision_skip_telegram_notify",
        settings.decision_skip_telegram_notify,
        "telegram on decision BUY skips",
    )
    row(
        "PORTFOLIO_STATUS_HEARTBEAT_SEC (const)",
        C.PORTFOLIO_STATUS_HEARTBEAT_SEC,
        "full STATUS telegram at least this often while running",
    )
    row(
        "PORTFOLIO_TELEGRAM_MOMENTUM_LONG_SEC (const)",
        C.PORTFOLIO_TELEGRAM_MOMENTUM_LONG_SEC,
        "portfolio Telegram second momentum line (2h window, display only)",
    )

    sec("Momentum engine (constants.py · used by decision_core / momentum_engine)")
    row(
        "MOMENTUM_WINDOW_SECONDS",
        C.MOMENTUM_WINDOW_SECONDS,
        "15m window sec for mom / fast exit / competitor / entry rise",
    )
    row(
        "MOMENTUM_FAST_EXIT_DROP",
        C.MOMENTUM_FAST_EXIT_DROP,
        "drop in window → fast stop-loss exit",
    )
    row(
        "MOMENTUM_COMPETITOR_SURGE",
        C.MOMENTUM_COMPETITOR_SURGE,
        "sibling rise → exit our bucket",
    )
    row(
        "MOMENTUM_ENTRY_RISE",
        C.MOMENTUM_ENTRY_RISE,
        "YES rise in window → momentum entry (bypasses normal competition gap)",
    )
    row(
        "TIME_DECAY_HOURS",
        C.TIME_DECAY_HOURS,
        "held longer than this → time-decay exit checks",
    )
    row(
        "TIME_DECAY_MIN_GAIN",
        C.TIME_DECAY_MIN_GAIN,
        "min gain fraction to avoid time-decay exit",
    )
    row(
        "TIME_DECAY_MAX_PRICE",
        C.TIME_DECAY_MAX_PRICE,
        "max mark for time-decay exit arm",
    )

    sec("Other code defaults (constants.py)")
    row(
        "BUY_EARLIEST_HOUR", C.BUY_EARLIEST_HOUR, "first local hour (city) for new buys"
    )
    row(
        "MAX_CONCURRENT_POSITIONS",
        C.MAX_CONCURRENT_POSITIONS,
        "max open positions across portfolio",
    )
    row(
        "BUY_BLOCK_EVENT_DATE_AFTER_ISRAEL_TODAY",
        C.BUY_BLOCK_EVENT_DATE_AFTER_ISRAEL_TODAY,
        "block buys for event dates after Israel today",
    )
    row(
        "ENABLE_MOMENTUM (legacy flag)",
        C.ENABLE_MOMENTUM,
        "legacy momentum flag; smart path uses window above",
    )

    return r


def build_startup_strategy_digest_plain(settings: RuntimeSettings) -> str:
    lines: List[str] = []
    lines.append("Strategy settings digest (startup · effective + key constants)")
    lines.append("See STRATEGY_LOGIC.md and config/constants.py in repo.")
    lines.append("")
    for title, val, meaning in _digest_rows(settings):
        if val is None:
            lines.append("")
            lines.append(f"--- {title} ---")
            continue
        lines.append(f"{title}={val} — {meaning}")
    return "\n".join(lines)


def build_startup_strategy_digest_html(settings: RuntimeSettings) -> str:
    lines: List[str] = []
    lines.append("📋 <b>Strategy settings digest</b> <i>(startup)</i>")
    lines.append(
        "<i>Effective = <code>data/runtime_config.json</code> merged on defaults from "
        "<code>config/constants.py</code>. BUY/SKIP/EXIT table: <code>STRATEGY_LOGIC.md</code>.</i>"
    )
    lines.append("")
    for title, val, meaning in _digest_rows(settings):
        if val is None:
            lines.append("")
            lines.append(f"<b>{tg_escape(title)}</b>")
            continue
        lines.append(
            f"<code>{tg_escape(title)}</code>=<b>{tg_escape(val)}</b> — "
            f"<i>{tg_escape(meaning or '')}</i>"
        )
    return "\n".join(lines)
