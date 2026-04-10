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
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import main as bot_main
from app.dashboard import dashboard_router

_DOTENV_PATH = Path(__file__).resolve().parent / ".env"
if _DOTENV_PATH.is_file():
    load_dotenv(_DOTENV_PATH, override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("main_bot")

BOT_THREAD_RESTART_DELAY_SEC = 60

# runs as soon as the worker imports this module (visible in Cloud Run *application* logs)
log.info(
    "main_bot imported pid=%s PORT=%s K_SERVICE=%s",
    os.getpid(),
    os.environ.get("PORT", ""),
    os.environ.get("K_SERVICE", ""),
)
print("main_bot: module loaded (stdout)", flush=True)


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
    log.info("FastAPI lifespan: startup — spawning bot thread")
    print("main_bot: lifespan startup (stdout)", flush=True)
    start_bot_background()
    yield
    log.info("FastAPI lifespan: shutdown")


app = FastAPI(lifespan=lifespan)
app.include_router(dashboard_router)

_UI_DIST = Path(__file__).resolve().parent / "ui" / "dist"
if _UI_DIST.is_dir():
    app.mount(
        "/dashboard",
        StaticFiles(directory=str(_UI_DIST), html=True),
        name="dashboard",
    )


@app.get("/")
def root():
    # UI is built with vite base=/dashboard/ — root is only plain health if dist missing
    if _UI_DIST.is_dir():
        return RedirectResponse(url="/dashboard/", status_code=302)
    return PlainTextResponse(
        "Bot is running (no ui/dist — run: cd ui && npm run build)\n"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log.info("uvicorn starting host=0.0.0.0 port=%s", port)
    print(f"main_bot: uvicorn bind 0.0.0.0:{port} (stdout)", flush=True)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )
