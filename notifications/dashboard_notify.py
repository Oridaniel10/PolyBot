import os
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

from config.constants import ENV_FILE
from telegram_bot import TelegramBot, tg_escape


def _telegram() -> TelegramBot:
    load_dotenv(ENV_FILE, override=False)
    return TelegramBot(
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
        os.getenv("TELEGRAM_CHAT_ID", ""),
    )


def notify_dashboard_change(title: str, body_lines: List[str]) -> None:
    """
    best-effort telegram when dashboard changes config / blacklist.
    """
    bot = _telegram()
    if not bot.is_configured():
        return
    parts = [f"⚙️ <b>{tg_escape(title)}</b>"]
    for line in body_lines:
        parts.append(tg_escape(line))
    try:
        bot.send_html_chunks("\n".join(parts))
    except requests.RequestException:
        pass


def format_runtime_patch_summary(patch: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for k in sorted(patch.keys()):
        v = patch[k]
        if k == "blacklist_market_ids" and isinstance(v, list):
            lines.append(f"blacklist_market_ids count={len(v)}")
            continue
        if isinstance(v, bool):
            lines.append(f"{k}={v}")
        elif isinstance(v, float):
            lines.append(f"{k}={v:.6g}")
        else:
            lines.append(f"{k}={v!r}")
    return lines[:40] if len(lines) > 40 else lines
