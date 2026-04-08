"""
Google Cloud Run entry: HTTP health on the main thread, bot in a background thread.

Cloud Run checklist (otherwise the bot looks "dead"):
- Set ALL secrets as environment variables on the service (there is no .env in the image).
- Turn ON "CPU is always allocated" (or equivalent). Default Cloud Run only gives CPU
  while handling HTTP requests — the background bot thread will barely run without this.
- Optional: min instances = 1 if you want the container warm 24/7.

Dockerfile CMD:
  CMD ["python", "main_bot.py"]

Or: uvicorn main_bot:app --host 0.0.0.0 --port ${PORT}
"""

import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

import main as bot_main

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("main_bot")

BOT_THREAD_RESTART_DELAY_SEC = 60


def log_cloud_env_hint() -> None:
    need = (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "POLY_PRIVATE_KEY",
        "API_KEY",
        "API_SECRET",
        "API_PASSPHRASE",
    )
    missing = [k for k in need if not (os.environ.get(k) or "").strip()]
    if missing:
        log.warning(
            "missing env vars (no Telegram / trading until set on Cloud Run): %s",
            ", ".join(missing),
        )
    else:
        log.info("core env vars present (telegram + clob keys)")


def run_bot_forever() -> None:
    while True:
        try:
            log.info("polymarket bot thread: starting run_bot()")
            bot_main.run_bot()
        except Exception:
            log.exception(
                "polymarket bot thread crashed (check env, keys, network). "
                "restarting in %ss",
                BOT_THREAD_RESTART_DELAY_SEC,
            )
            time.sleep(BOT_THREAD_RESTART_DELAY_SEC)


def start_bot_background() -> None:
    log_cloud_env_hint()
    thread = threading.Thread(
        target=run_bot_forever, name="polymarket-bot", daemon=True
    )
    thread.start()
    log.info("polymarket bot background thread started (name=%s)", thread.name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_bot_background()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "Bot is running"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log.info("listening on 0.0.0.0:%s (set PORT on Cloud Run)", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
