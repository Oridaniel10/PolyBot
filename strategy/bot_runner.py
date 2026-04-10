import os
import sys
import threading
import time
from pathlib import Path

# repo root on sys.path when launched as `python strategy/bot_runner.py`
if not __package__:
    _root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_root))

import requests

from config.constants import (
    ENV_FILE,
    TERM_BOLD,
    TERM_CYAN,
    TERM_DIM,
    TERM_RED,
    TERM_RESET,
    TERM_YELLOW,
)
from config.settings import (
    default_runtime_dict,
    get_effective_settings,
    load_runtime_config_file,
)
from notifications.portfolio import (
    send_daily_report,
    send_hourly_summary_report,
    send_portfolio_telegram,
    should_send_hourly_summary,
    should_send_report,
)
from notifications.terminal import (
    log_sleep_terminal,
    term_wrap,
)
from polymarket_client import PolymarketClient, PolymarketConfig
from state.store import load_env_file, read_state, write_state
from strategy.fingerprint import portfolio_snapshot_fingerprint
from strategy.momentum import prune_old_price_sample_files
from strategy.loop import run_once
from strategy.sync_portfolio import sync_state_with_portfolio
from strategy.time_utils import format_report_local_hhmm, now_in_report_timezone
from telegram_bot import TelegramBot, tg_escape


def build_config() -> PolymarketConfig:
    sig_env = os.getenv("POLY_SIGNATURE_TYPE", "").strip()
    clob_sig = int(sig_env) if sig_env.isdigit() else None
    return PolymarketConfig(
        api_key=os.getenv("API_KEY", os.getenv("POLY_API_KEY", "")),
        api_secret=os.getenv("API_SECRET", ""),
        api_passphrase=os.getenv("API_PASSPHRASE", ""),
        private_key=os.getenv("POLY_PRIVATE_KEY", os.getenv("Wallet_PRIVATE_KEY", "")),
        proxy_address=os.getenv("POLY_PROXY_ADDRESS", os.getenv("POLY_ADDRESS", "")),
        gamma_base_url=os.getenv(
            "POLY_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"
        ),
        clob_base_url=os.getenv("POLY_CLOB_BASE_URL", "https://clob.polymarket.com"),
        clob_signature_type=clob_sig,
    )


def log_settings_on_startup() -> None:
    defaults = default_runtime_dict()
    loaded = load_runtime_config_file()
    overrides = []
    for key in sorted(defaults):
        dv = defaults[key]
        lv = loaded.get(key, dv)
        if lv != dv:
            overrides.append((key, dv, lv))
    sep = "─" * 72
    print(f"\n{TERM_BOLD}┌{sep}┐{TERM_RESET}")
    print(f"{TERM_BOLD}│  SETTINGS  ·  runtime_config.json vs constants.py{TERM_RESET}")
    print(f"{TERM_BOLD}├{sep}┤{TERM_RESET}")
    if not overrides:
        print(f"│  {TERM_CYAN}all values match constants defaults{TERM_RESET}")
    else:
        print(
            f"│  {TERM_YELLOW}{len(overrides)} override(s) from runtime_config.json:{TERM_RESET}"
        )
        for key, dv, lv in overrides:
            print(
                f"│    {key}: {TERM_DIM}default={dv!r}{TERM_RESET} → "
                f"{TERM_YELLOW}{lv!r}{TERM_RESET}"
            )
    print(f"{TERM_BOLD}└{sep}┘{TERM_RESET}\n", flush=True)


def run_bot() -> None:
    load_env_file(ENV_FILE)
    prune_old_price_sample_files()
    log_settings_on_startup()
    get_effective_settings()
    config = build_config()
    telegram = TelegramBot(
        token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
    client = PolymarketClient(config)
    state = read_state()
    try:
        state = sync_state_with_portfolio(client, state)
        write_state(state)
    except Exception as err:
        print(term_wrap(TERM_RED, f"[startup sync failed] {err!r}"))
    try:
        send_portfolio_telegram(
            telegram,
            client,
            "bot started — synced portfolio (cash + positions)",
            echo_terminal=True,
            terminal_title="bot started — portfolio snapshot",
            headline_html="🚀 <b>Bot started</b> · synced portfolio snapshot",
            state=state,
            blacklist_ids=get_effective_settings().blacklist_market_ids,
        )
    except Exception as err:
        print(term_wrap(TERM_RED, f"[startup portfolio message failed] {err!r}"))
    try:
        state["last_portfolio_fingerprint"] = portfolio_snapshot_fingerprint(client)
        write_state(state)
    except Exception as err:
        print(term_wrap(TERM_RED, f"[startup fingerprint failed] {err!r}"))

    while True:
        now = now_in_report_timezone()
        try:
            state = sync_state_with_portfolio(client, state)
            fp = portfolio_snapshot_fingerprint(client)
            prev_fp = str(state.get("last_portfolio_fingerprint") or "")
            tracked_count = len(state.get("active_trades", {}))
            # only alert on structural change when we actually have
            # positions — avoids false alerts from stale/empty API reads
            if (
                telegram.is_configured()
                and prev_fp
                and fp != prev_fp
                and tracked_count > 0
            ):
                threading.Thread(
                    target=send_portfolio_telegram,
                    args=(
                        telegram,
                        client,
                        "PORTFOLIO STRUCTURE CHANGE — manual/UI (positions, sizes, or cash)",
                    ),
                    kwargs={
                        "echo_terminal": True,
                        "terminal_title": "portfolio structure change (external)",
                        "headline_html": (
                            "⚡ <b>Manual / UI trade</b>\n"
                            "<i>positions, share size, avg entry, or cash changed — "
                            "not mark-only</i>"
                        ),
                        "state": state,
                        "blacklist_ids": get_effective_settings().blacklist_market_ids,
                    },
                    daemon=True,
                ).start()
            state = run_once(client, telegram, state)
            if should_send_hourly_summary(state, now):
                state["last_hourly_summary_slot"] = now.strftime("%Y-%m-%d-%H-%M")
                threading.Thread(
                    target=send_hourly_summary_report,
                    args=(telegram, client, state, now),
                    daemon=True,
                ).start()
            if should_send_report(state, now):
                state["last_report_sent"] = now.strftime("%Y-%m-%d-%H")
                threading.Thread(
                    target=send_daily_report,
                    args=(telegram, client, state),
                    daemon=True,
                ).start()
        except Exception as error:
            print(term_wrap(TERM_RED, f"[loop error] {error!r}"))
            try:
                if telegram.is_configured():
                    telegram.send_html_chunks(
                        f"🔴 <b>bot loop error</b>\n"
                        f"🕐 <code>{tg_escape(format_report_local_hhmm())}</code>\n"
                        f"<pre>{tg_escape(repr(error))}</pre>"
                    )
            except requests.RequestException:
                pass
        finally:
            try:
                state["last_portfolio_fingerprint"] = portfolio_snapshot_fingerprint(
                    client
                )
                write_state(state)
            except Exception as err:
                print(term_wrap(TERM_RED, f"[persist state failed] {err!r}"))
        # --- check for Telegram commands ---
        try:
            for cmd in telegram.poll_commands():
                cmd_lower = cmd.lower().strip()
                if cmd_lower in ("/status", "/report", "status", "report", "דוח"):
                    _bl = get_effective_settings().blacklist_market_ids
                    threading.Thread(
                        target=send_portfolio_telegram,
                        args=(telegram, client, "on-demand status report"),
                        kwargs={
                            "headline_html": "📋 <b>On-demand status report</b>",
                            "state": state,
                            "blacklist_ids": _bl,
                        },
                        daemon=True,
                    ).start()
                elif cmd_lower in ("/help", "help", "עזרה"):
                    telegram.send_html_chunks(
                        "📖 <b>Commands:</b>\n"
                        "<code>/status</code> — portfolio report\n"
                        "<code>/help</code> — this message"
                    )
        except Exception:
            pass
        interval = get_effective_settings().scan_interval_seconds
        log_sleep_terminal(interval)
        time.sleep(interval)


def main() -> None:
    run_bot()


if __name__ == "__main__":
    main()
