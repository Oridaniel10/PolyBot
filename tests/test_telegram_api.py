import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from telegram_bot import TelegramBot  # noqa: E402


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        value = raw_value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    load_env_file(PROJECT_ROOT / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    bot = TelegramBot(token=token, chat_id=chat_id)

    if not bot.is_configured():
        print("telegram config missing: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return

    message = (
        f"telegram api test from polymarket bot at {datetime.utcnow().isoformat()}Z"
    )
    result = bot.send_message(message)
    print("telegram response ok:", result.get("ok"))


if __name__ == "__main__":
    main()
