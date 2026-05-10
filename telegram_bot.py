import html
import time
from typing import Any, Dict, List, Optional

import requests


TELEGRAM_TIMEOUT_SECONDS = 15
TELEGRAM_MAX_CHUNK_CHARS = 3800
# stay under Telegram 4096 hard limit (leave headroom for entities)
TELEGRAM_HTML_CHUNK_CHARS = 4040
TELEGRAM_SEND_MESSAGE_MAX = 4096

_LAST_GETUPDATES_LOG_MONO = 0.0


def split_html_telegram_chunks(html_text: str, max_size: int) -> List[str]:
    """
    pack whole lines (split on \\n) into chunks under max_size so we do not cut
    mid-tag (Telegram HTML parse errors).
    """
    if not html_text:
        return []
    lines = html_text.split("\n")
    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0

    def flush() -> None:
        nonlocal cur, cur_len
        if cur:
            chunks.append("\n".join(cur))
            cur = []
            cur_len = 0

    for ln in lines:
        ln_len = len(ln)
        if ln_len > max_size:
            flush()
            for i in range(0, ln_len, max_size):
                chunks.append(ln[i : i + max_size])
            continue
        if cur_len + ln_len + (1 if cur else 0) > max_size and cur:
            flush()
        add = ln_len + (1 if cur else 0)
        cur.append(ln)
        cur_len += add
    flush()
    return chunks


def normalize_telegram_command_text(text: str) -> str:
    """
    first non-empty line, lowercased; strips @BotUsername from /commands (Telegram default).
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    s = lines[0].lower().strip()
    if s.startswith("/") and "@" in s:
        s = s.split("@", 1)[0].strip()
    return s


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
        except Exception as exc:
            global _LAST_GETUPDATES_LOG_MONO
            now_m = time.monotonic()
            if now_m - _LAST_GETUPDATES_LOG_MONO > 60.0:
                _LAST_GETUPDATES_LOG_MONO = now_m
                print(f"[telegram] getUpdates failed: {exc!r}", flush=True)
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

    def send_plain_by_lines(self, text: str, max_size: int = TELEGRAM_SEND_MESSAGE_MAX) -> int:
        """like send_text_chunks but splits on newlines so chunks stay readable."""
        if not self.is_configured() or not text:
            return 0
        sent = 0
        for chunk in split_html_telegram_chunks(text, max(500, min(max_size, TELEGRAM_SEND_MESSAGE_MAX))):
            try:
                self.send_message(chunk)
                sent += 1
            except requests.RequestException as exc:
                print(
                    f"[telegram] send_plain_by_lines failed after {sent} chunk(s): {exc!r}",
                    flush=True,
                )
                break
        return sent

    def send_document_text(
        self,
        filename: str,
        text: str,
        caption: str = "",
    ) -> bool:
        """upload UTF-8 text as a document (one chat bubble + file)."""
        if not self.is_configured():
            return False
        raw = (text or "").encode("utf-8")
        if not raw:
            return False
        files = {"document": (filename, raw, "text/plain; charset=utf-8")}
        data: Dict[str, Any] = {"chat_id": self.chat_id}
        cap = (caption or "").strip()
        if cap:
            data["caption"] = cap[:1024]
        try:
            resp = requests.post(
                f"{self.base_url}/sendDocument",
                files=files,
                data=data,
                timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            return True
        except requests.RequestException as exc:
            print(f"[telegram] sendDocument failed: {exc!r}", flush=True)
            return False

    def send_photo(
        self,
        photo_path: str,
        caption: str = "",
        *,
        message_thread_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """upload image (PNG/JPEG). optional forum topic via message_thread_id."""
        from pathlib import Path

        if not self.is_configured():
            return {"ok": False, "error": "telegram is not configured"}
        path = Path(photo_path)
        if not path.is_file():
            return {"ok": False, "error": "photo file missing"}
        data: Dict[str, Any] = {"chat_id": self.chat_id}
        cap = (caption or "").strip()
        if cap:
            data["caption"] = cap[:1024]
            data["parse_mode"] = "HTML"
        mtid = message_thread_id
        if mtid is not None and int(mtid) > 0:
            data["message_thread_id"] = int(mtid)
        try:
            with path.open("rb") as ph:
                resp = requests.post(
                    f"{self.base_url}/sendPhoto",
                    data=data,
                    files={"photo": ph},
                    timeout=TELEGRAM_TIMEOUT_SECONDS,
                )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            print(f"[telegram] sendPhoto failed: {exc!r}", flush=True)
            return {"ok": False, "error": str(exc)}

    def send_html_chunks(self, html_text: str, chunk_size: int = TELEGRAM_HTML_CHUNK_CHARS) -> int:
        if not self.is_configured() or not html_text:
            return 0
        sent = 0
        for chunk in split_html_telegram_chunks(html_text, chunk_size):
            try:
                self.send_message(chunk, parse_mode="HTML")
                sent += 1
            except requests.RequestException as exc:
                print(
                    f"[telegram] send_html_chunks failed after {sent} chunk(s): {exc!r}",
                    flush=True,
                )
                break
        return sent
