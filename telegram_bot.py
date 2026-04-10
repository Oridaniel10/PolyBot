import html
from typing import Any, Callable, Dict, List, Optional

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
        self._last_update_id: int = 0

    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def get_updates(self, timeout: int = 1) -> List[Dict[str, Any]]:
        if not self.is_configured():
            return []
        try:
            resp = requests.get(
                f"{self.base_url}/getUpdates",
                params={"offset": self._last_update_id + 1, "timeout": timeout},
                timeout=timeout + 5,
            )
            resp.raise_for_status()
            data = resp.json()
            updates = data.get("result", [])
            if updates:
                self._last_update_id = max(u.get("update_id", 0) for u in updates)
            return updates
        except Exception:
            return []

    def poll_commands(self) -> List[str]:
        """Poll for new text messages from the configured chat. Returns list of message texts."""
        updates = self.get_updates(timeout=0)
        messages: List[str] = []
        for u in updates:
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id_str = str(chat.get("id") or "")
            if chat_id_str != str(self.chat_id):
                continue
            text = (msg.get("text") or "").strip()
            if text:
                messages.append(text)
        return messages

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
