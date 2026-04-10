import html
from typing import Any, Dict, Optional

import requests


TELEGRAM_TIMEOUT_SECONDS = 15
TELEGRAM_MAX_CHUNK_CHARS = 3800
TELEGRAM_HTML_CHUNK_CHARS = 3500


def tg_escape(text: object) -> str:
    return html.escape(str(text), quote=False)


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

    def send_text_chunks(self, text: str, chunk_size: int = TELEGRAM_MAX_CHUNK_CHARS) -> int:
        """
        sends long text in multiple messages (Telegram ~4096 char limit).
        returns number of chunks successfully sent.
        """
        if not self.is_configured() or not text:
            return 0
        sent = 0
        for start in range(0, len(text), chunk_size):
            chunk = text[start : start + chunk_size]
            try:
                self.send_message(chunk)
                sent += 1
            except requests.RequestException:
                break
        return sent

    def send_html_chunks(self, html_text: str, chunk_size: int = TELEGRAM_HTML_CHUNK_CHARS) -> int:
        if not self.is_configured() or not html_text:
            return 0
        sent = 0
        for start in range(0, len(html_text), chunk_size):
            chunk = html_text[start : start + chunk_size]
            try:
                self.send_message(chunk, parse_mode="HTML")
                sent += 1
            except requests.RequestException:
                break
        return sent
