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
from notifications.forecast_quick_report import build_open_temp_forecast_quick_html
from notifications.portfolio import (
    maybe_send_portfolio_status_heartbeat,
    send_daily_report,
    send_hourly_summary_report,
    send_portfolio_telegram,
    should_send_hourly_summary,
    should_send_report,
)
from notifications.startup_digest import build_startup_strategy_digest_plain
from notifications.terminal import (
    log_sleep_terminal,
    term_wrap,
)
from polymarket_client import PolymarketClient, PolymarketConfig
from state.store import load_env_file, read_state, write_state
from strategy.fingerprint import portfolio_snapshot_fingerprint
from strategy.momentum import prune_old_price_sample_files, warm_ring_buffer_from_disk
from strategy import redis_store
from forecast.digest_runner import (
    run_forecast_digest_once,
    start_forecast_digest_background,
)
from strategy.loop import run_once
from strategy.sync_portfolio import sync_state_with_portfolio
from strategy.time_utils import format_report_local_hhmm, now_in_report_timezone
from telegram_bot import TelegramBot, normalize_telegram_command_text, tg_escape

from notifications.openrouter_advisor import (
    advisor_enabled,
    normalize_question_for_advisor,
    should_route_message_to_advisor,
    start_advisor_process,
)

_ADVISOR_LAST_MONO = 0.0
_TELEGRAM_POLL_INTERVAL_SEC = 2


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


def _safe_send_portfolio_telegram(*args, **kwargs):
    telegram = args[0] if args else kwargs.get("telegram")
    try:
        send_portfolio_telegram(*args, **kwargs)
    except Exception as e:
        import traceback
        traceback.print_exc()
        if telegram:
            try:
                telegram.send_message(f"🔴 Error generating report: {e!r}")
            except Exception:
                pass

def dispatch_telegram_commands(
    telegram: TelegramBot,
    client: PolymarketClient,
    state: dict,
) -> None:
    """poll getUpdates and react; safe to call more than once per loop iteration."""
    global _ADVISOR_LAST_MONO
    try:
        for cmd in telegram.poll_commands():
            cmd_lower = normalize_telegram_command_text(cmd)
            if not cmd_lower:
                continue
            if cmd_lower in ("/status", "/report", "status", "report", "דוח"):
                print(
                    term_wrap(TERM_DIM, f"[telegram] on-demand command: {cmd_lower!r}")
                )
                _bl = get_effective_settings().blacklist_market_ids
                threading.Thread(
                    target=_safe_send_portfolio_telegram,
                    args=(telegram, client, "on-demand status report"),
                    kwargs={
                        "headline_html": (
                            "<b>📊 STATUS</b> · <i>on-demand</i>\n"
                            "📋 <b>On-demand status report</b>"
                        ),
                        "state": state,
                        "blacklist_ids": _bl,
                    },
                    daemon=True,
                ).start()
            elif cmd_lower in ("/forecast", "/fc", "forecast"):
                threading.Thread(
                    target=lambda: telegram.send_html_chunks(
                        build_open_temp_forecast_quick_html(client)
                    ),
                    daemon=True,
                ).start()
            elif cmd_lower in ("/digest", "/forecast_digest", "digest"):
                if telegram.is_configured():
                    try:
                        telegram.send_message(
                            "⏳ Building full forecast digest (all parsable city-days). "
                            "May take 1–3 minutes; messages will follow in chunks."
                        )
                    except Exception:
                        pass

                def run_full_digest() -> None:
                    run_forecast_digest_once(
                        client,
                        telegram,
                        force=True,
                        send_telegram=True,
                    )

                threading.Thread(target=run_full_digest, daemon=True).start()
            elif cmd_lower in ("/help", "help", "עזרה"):
                telegram.send_html_chunks(
                    "📖 <b>Commands:</b>\n"
                    "<code>/status</code> — portfolio report\n"
                    "<code>/forecast</code> — all open positions + OM/calibration per row\n"
                    "<code>/digest</code> — full forecast digest, all city-days (Telegram, one shot)\n"
                    "<code>/ask …</code> — OpenRouter advisor (needs <code>OPENROUTER_API_KEY</code>)\n"
                    "<code>/help</code> — this message\n"
                    "<i>Advisor also listens when your message contains the word "
                    "<code>bot</code> or <code>BOT</code> (separate OS process).</i>\n"
                    "<i>Optional: <code>ADVISOR_CATCHALL_NON_COMMANDS=1</code> routes other text.</i>\n"
                    "<i>Tip: Telegram may send <code>/report@YourBot</code> — that works too.</i>"
                )
            elif advisor_enabled() and should_route_message_to_advisor(cmd):
                q = normalize_question_for_advisor(cmd)
                if len(q.strip()) < 2:
                    try:
                        telegram.send_message(
                            "Advisor: add a question (e.g. /ask why … or mention bot in your text)."
                        )
                    except Exception:
                        pass
                    continue
                cd = float(os.getenv("ADVISOR_COOLDOWN_SEC", "45") or 45)
                now_m = time.monotonic()
                if now_m - _ADVISOR_LAST_MONO < cd:
                    try:
                        telegram.send_message(
                            f"Advisor: wait ~{int(cd - (now_m - _ADVISOR_LAST_MONO))}s before another question."
                        )
                    except Exception:
                        pass
                    continue
                _ADVISOR_LAST_MONO = now_m
                try:
                    telegram.send_message(
                        "⌛ Advisor (OpenRouter) is gathering repo context in a separate process…"
                    )
                except Exception:
                    pass
                start_advisor_process(q)
    except Exception as exc:
        print(term_wrap(TERM_RED, f"[telegram command poll] {exc!r}"))


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


def _telegram_command_poller(
    telegram: TelegramBot,
    client: PolymarketClient,
    state: dict,
) -> None:
    """Dedicated thread polling Telegram every 2s for instant command response."""
    while True:
        try:
            dispatch_telegram_commands(telegram, client, state)
        except Exception:
            pass
        time.sleep(_TELEGRAM_POLL_INTERVAL_SEC)


def run_bot() -> None:
    load_env_file(ENV_FILE)
    redis_store.connect()
    prune_old_price_sample_files()
    warm_ring_buffer_from_disk()
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
        state = sync_state_with_portfolio(client, state, telegram=telegram)
        write_state(state)
    except Exception as err:
        print(term_wrap(TERM_RED, f"[startup sync failed] {err!r}"))
    try:
        st0 = get_effective_settings()
        digest_plain = build_startup_strategy_digest_plain(st0)
        send_portfolio_telegram(
            telegram,
            client,
            "bot started — synced portfolio (cash + positions)",
            echo_terminal=True,
            terminal_title="bot started — portfolio snapshot",
            headline_html=(
                "<b>📊 STATUS · startup</b>\n"
                "🚀 <b>Bot started</b> · synced portfolio snapshot"
            ),
            state=state,
            blacklist_ids=st0.blacklist_market_ids,
            footer_plain=digest_plain,
        )
    except Exception as err:
        print(term_wrap(TERM_RED, f"[startup portfolio message failed] {err!r}"))
    try:
        prev_fp0 = str(state.get("last_portfolio_fingerprint") or "")
        state["last_portfolio_fingerprint"] = portfolio_snapshot_fingerprint(
            client, previous_on_error=prev_fp0
        )
        state.setdefault("last_status_heartbeat_ts", 0.0)
        state["last_status_heartbeat_ts"] = time.time()
        write_state(state)
    except Exception as err:
        print(term_wrap(TERM_RED, f"[startup fingerprint failed] {err!r}"))

    if get_effective_settings().forecast_digest_enabled:
        try:
            start_forecast_digest_background(telegram, config)
            print(
                term_wrap(
                    TERM_DIM,
                    "[forecast] background digest thread started (Telegram every N min)",
                )
            )
        except Exception as err:
            print(term_wrap(TERM_RED, f"[forecast digest thread failed] {err!r}"))

    # fast exit watcher: daemon thread polling CLOB price every ~5 seconds
    from strategy.fast_exit_watcher import FastExitWatcher
    _fast_watcher = FastExitWatcher(
        client=client,
        state_ref=state,
        telegram=telegram,
        get_settings_fn=get_effective_settings,
        save_state_fn=lambda: write_state(state),
    )
    _fast_watcher.start()

    # dedicated Telegram polling thread — responds to /report /ask within 2s
    threading.Thread(
        target=_telegram_command_poller,
        args=(telegram, client, state),
        daemon=True,
        name="telegram_poller",
    ).start()

    while True:
        now = now_in_report_timezone()
        interrupted = False
        try:
            state = sync_state_with_portfolio(client, state, telegram=telegram)
            prev_fp = str(state.get("last_portfolio_fingerprint") or "")
            fp = portfolio_snapshot_fingerprint(client, previous_on_error=prev_fp)
            tracked_count = len(state.get("active_trades", {}))
            # only alert on structural change when we actually have
            # positions — avoids false alerts from stale/empty API reads
            if (
                telegram.is_configured()
                and prev_fp
                and fp
                and fp != prev_fp
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
                            "<b>📊 STATUS</b> · <i>portfolio change</i>\n"
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
        except KeyboardInterrupt:
            interrupted = True
            print(
                term_wrap(
                    TERM_YELLOW,
                    "\n[stopped] Ctrl+C — exiting (no second API call in finally).",
                ),
                flush=True,
            )
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
                if interrupted:
                    write_state(state)
                else:
                    try:
                        prev_save = str(state.get("last_portfolio_fingerprint") or "")
                        state["last_portfolio_fingerprint"] = (
                            portfolio_snapshot_fingerprint(
                                client, previous_on_error=prev_save
                            )
                        )
                        write_state(state)
                    except KeyboardInterrupt:
                        print(
                            term_wrap(
                                TERM_YELLOW,
                                "\n[stopped] Ctrl+C during save — exiting.",
                            ),
                            flush=True,
                        )
                        interrupted = True
                        try:
                            write_state(state)
                        except Exception:
                            pass
            except Exception as err:
                print(term_wrap(TERM_RED, f"[persist state failed] {err!r}"))
        if interrupted:
            return
        # telegram commands now handled by dedicated polling thread
        try:
            if maybe_send_portfolio_status_heartbeat(telegram, client, state):
                write_state(state)
        except Exception as hb_err:
            print(term_wrap(TERM_DIM, f"[status heartbeat] {hb_err!r}"))
        interval = get_effective_settings().scan_interval_seconds
        log_sleep_terminal(interval)
        time.sleep(interval)


def main() -> None:
    run_bot()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(term_wrap(TERM_YELLOW, "\n[stopped] Ctrl+C"), flush=True)
        sys.exit(0)
