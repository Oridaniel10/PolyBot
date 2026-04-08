from typing import Any, Dict, Optional

import requests


TELEGRAM_TIMEOUT_SECONDS = 15


class TelegramBot:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"

    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_message(self, text: str, parse_mode: Optional[str] = None) -> Dict[str, Any]:
        if not self.is_configured():
            return {"ok": False, "error": "telegram is not configured"}

        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        response = requests.post(
            f"{self.base_url}/sendMessage",
            json=payload,
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
