"""Standalone: `python -m forecast` — Telegram forecast digest loop only (no trading)."""

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config.constants import ENV_FILE
from config.settings import get_effective_settings
from forecast.digest_runner import (
    forecast_digest_loop_runner,
    run_forecast_digest_once,
)
from polymarket_client import PolymarketClient
from state.store import load_env_file
from strategy.bot_runner import build_config
from telegram_bot import TelegramBot


def main() -> None:
    load_env_file(ENV_FILE)
    get_effective_settings()
    telegram = TelegramBot(
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
        os.getenv("TELEGRAM_CHAT_ID", ""),
    )
    cfg = build_config()
    eff = get_effective_settings()
    try:
        ref = max(
            60,
            min(3600, int(getattr(eff, "forecast_digest_refresh_interval_sec", 120))),
        )
        tg_iv = max(0, min(86400, int(getattr(eff, "forecast_digest_telegram_interval_sec", 0))))
    except Exception:
        ref = 120
        tg_iv = 0
    scope = str(getattr(eff, "forecast_digest_scope", "scan") or "scan")
    print(
        f"forecast standalone: refresh={ref}s telegram={tg_iv}s (0=no auto send) scope={scope} · "
        "runtime forecast_digest_enabled is ignored (for bot+digest off, use this process).",
        flush=True,
    )
    if not telegram.is_configured():
        print(
            "forecast standalone: telegram not configured — still writes "
            "data/forecast_cache.json (no Telegram send).",
            flush=True,
        )

    if os.environ.get("FORECAST_DIGEST_ONCE", "").strip() in ("1", "true", "yes"):
        print("FORECAST_DIGEST_ONCE: one digest then exit.", flush=True)
        client = PolymarketClient(cfg)
        run_forecast_digest_once(
            client,
            telegram,
            standalone=True,
            log_print=True,
            send_telegram=True,
        )
        print("FORECAST_DIGEST_ONCE: done.", flush=True)
        return

    forecast_digest_loop_runner(telegram, cfg, standalone=True)


if __name__ == "__main__":
    main()
