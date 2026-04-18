"""
Telegram Q&A via OpenRouter (separate process from main bot loop).

trigger: /ask …, or word "bot", or substring "BOT", or optional catch-all (env).
"""

from __future__ import annotations

import multiprocessing
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from telegram_bot import normalize_telegram_command_text

from config import constants as C
from config.settings import get_effective_settings
from notifications.startup_digest import build_startup_strategy_digest_plain

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# free-tier cascade: first successful HTTP 200 + non-empty assistant text wins.
# override with OPENROUTER_MODEL (single model id) to skip the list.
OPENROUTER_FREE_MODELS: List[str] = [
    "google/gemma-3-12b-it:free",
    "google/gemma-3-4b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemma-3n-4b-it:free",
    "google/gemma-3n-2b-it:free",
]
DEFAULT_MODEL = OPENROUTER_FREE_MODELS[0]

# first-line tokens handled by the main bot (do not send to advisor when catch-all is on)
_BUILTIN_FIRST_TOKENS = frozenset(
    {
        "/status",
        "/report",
        "/forecast",
        "/fc",
        "/digest",
        "/forecast_digest",
        "/help",
        "status",
        "report",
        "דוח",
        "forecast",
        "digest",
        "help",
        "עזרה",
    }
)

# caps keep free-tier context reasonable
_MAX_STRATEGY_CHARS = 55_000
_MAX_CONSTANTS_CHARS = 25_000
_MAX_SETTINGS_PY_CHARS = 35_000
_MAX_STATE_CHARS = 120_000
_MAX_RUNTIME_JSON_CHARS = 25_000
_MAX_LEDGER_TAIL_BYTES = 120_000
_MAX_TRADE_CSV_CHARS = 45_000
_MAX_TOTAL_CONTEXT_CHARS = 72_000


def openrouter_api_key() -> str:
    return (
        os.getenv("OPENROUTER_API_KEY", "").strip()
        or os.getenv("OPENROUTER", "").strip()
        or os.getenv("openrouter", "").strip()
    )


def advisor_enabled() -> bool:
    if os.getenv("ADVISOR_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return False
    return bool(openrouter_api_key())


def openrouter_model_candidates() -> List[str]:
    """ordered list of model ids to try; env OPENROUTER_MODEL forces a single id."""
    env = (os.getenv("OPENROUTER_MODEL") or "").strip()
    if env:
        return [env]
    return list(OPENROUTER_FREE_MODELS)


def openrouter_model() -> str:
    """first candidate (compat / display); full cascade via openrouter_model_candidates()."""
    c = openrouter_model_candidates()
    return c[0] if c else DEFAULT_MODEL


def advisor_catchall_non_commands() -> bool:
    return os.getenv("ADVISOR_CATCHALL_NON_COMMANDS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _read_head(path: Path, max_chars: int) -> str:
    if not path.is_file():
        return f"(missing file: {path.name})\n"
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(read error {path.name}: {exc})\n"
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + f"\n… [truncated, was {len(raw)} chars]\n"


def _read_tail_bytes(path: Path, max_bytes: int) -> str:
    if not path.is_file():
        return f"(missing file: {path.name})\n"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"(read error {path.name}: {exc})\n"
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace")
    chunk = data[-max_bytes:]
    return "… [start of file omitted]\n" + chunk.decode("utf-8", errors="replace")


def _trade_csv_today_text() -> str:
    try:
        from state.pnl_ledger import trade_csv_path_today

        p = trade_csv_path_today()
    except Exception:
        return "(no trade csv path)\n"
    return _read_head(p, _MAX_TRADE_CSV_CHARS)


def _context_specs() -> List[tuple[str, Path, int, str]]:
    """label, path, max_chars, mode: head|tail."""
    root = Path(__file__).resolve().parent.parent
    return [
        ("STRATEGY_LOGIC.md", root / "STRATEGY_LOGIC.md", _MAX_STRATEGY_CHARS, "head"),
        (
            "MODEL_PROBABILITY_AND_CALIBRATION.md",
            root / "MODEL_PROBABILITY_AND_CALIBRATION.md",
            _MAX_STRATEGY_CHARS,
            "head",
        ),
        (
            "constants.py",
            root / "config" / "constants.py",
            _MAX_CONSTANTS_CHARS,
            "head",
        ),
        (
            "settings.py",
            root / "config" / "settings.py",
            _MAX_SETTINGS_PY_CHARS,
            "head",
        ),
        ("runtime_config.json", C.RUNTIME_CONFIG_FILE, _MAX_RUNTIME_JSON_CHARS, "head"),
        ("state.json", C.STATE_FILE, _MAX_STATE_CHARS, "head"),
        ("pnl_ledger.jsonl (tail)", C.PNL_LEDGER_FILE, _MAX_LEDGER_TAIL_BYTES, "tail"),
    ]


def build_context_load_report() -> str:
    """one-line per source for logs / Telegram preamble (paths exist, bytes loaded)."""
    lines: List[str] = ["Context sources (advisor):"]
    for label, path, cap, mode in _context_specs():
        if not path.is_file():
            lines.append(f"  - {label}: MISSING ({path})")
            continue
        try:
            sz = path.stat().st_size
        except OSError as exc:
            lines.append(f"  - {label}: stat error {exc}")
            continue
        if mode == "tail":
            lines.append(
                f"  - {label}: ok, file {sz} bytes, sending up to {cap} tail bytes"
            )
        else:
            lines.append(
                f"  - {label}: ok, file {sz} bytes, sending up to {cap} chars (head)"
            )
    try:
        from state.pnl_ledger import trade_csv_path_today

        tcp = trade_csv_path_today()
        if tcp.is_file():
            lines.append(f"  - trades_csv_today: ok ({tcp.name})")
        else:
            lines.append("  - trades_csv_today: (no file yet today)")
    except Exception as exc:
        lines.append(f"  - trades_csv_today: error {exc}")
    lines.append("  - effective_settings_digest: from get_effective_settings()")
    lines.append(f"  - merged_blob_cap: {_MAX_TOTAL_CONTEXT_CHARS} chars")
    return "\n".join(lines)


def gather_advisor_context() -> str:
    parts: List[str] = []

    for label, path, cap, mode in _context_specs():
        parts.append(f"=== {label} ===\n")
        if label == "runtime_config.json" and not path.is_file():
            parts.append("(no runtime_config.json)\n")
            continue
        if mode == "tail":
            parts.append(_read_tail_bytes(path, cap))
        else:
            parts.append(_read_head(path, cap))

    parts.append("\n=== trades CSV today (if any) ===\n")
    parts.append(_trade_csv_today_text())

    parts.append("\n=== effective settings digest (plain) ===\n")
    try:
        parts.append(build_startup_strategy_digest_plain(get_effective_settings()))
    except Exception as exc:
        parts.append(f"(digest failed: {exc})\n")

    blob = "".join(parts)
    if len(blob) > _MAX_TOTAL_CONTEXT_CHARS:
        blob = (
            blob[:_MAX_TOTAL_CONTEXT_CHARS]
            + f"\n… [total context truncated to {_MAX_TOTAL_CONTEXT_CHARS} chars]\n"
        )
    return blob


SYSTEM_PROMPT = """You are the senior advisor for an automated Polymarket weather YES-bot (temperature buckets).

You receive: (1) STRATEGY_LOGIC.md, (2) MODEL_PROBABILITY_AND_CALIBRATION.md (forecast→μ, bias, σ, P(YES) with numeric examples), (3) Python defaults constants.py, (4) settings merge code, (5) runtime_config.json, (6) state.json, (7) recent ledger tail, (8) today's trade CSV if present, (9) a digest of effective runtime knobs.

Rules:
- Ground every claim in the pasted context. If something is not in the context, say you do not know.
- For "why did the bot skip buy" questions, map skip reasons to the entry table in STRATEGY_LOGIC.md and to research_edge / competition / forecast gates / momentum as appropriate.
- Be concise; use bullet lists. No financial advice disclaimer beyond: this is operational bot analysis, not personal investment advice.
- Answer in the same language as the user's question (Hebrew or English)."""


def _openrouter_headers() -> Dict[str, str]:
    key = openrouter_api_key()
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/polymarket-weather-bot",
        "X-Title": "poly weather bot advisor",
    }


def _post_openrouter_chat(payload: Dict[str, Any]) -> requests.Response:
    return requests.post(
        OPENROUTER_URL,
        headers=_openrouter_headers(),
        json=payload,
        timeout=120,
    )


def _openrouter_chat_once(payload: Dict[str, Any]) -> requests.Response:
    """single POST; no retries (429 returns immediately to caller)."""
    resp = _post_openrouter_chat(payload)
    resp.raise_for_status()
    return resp


def _assistant_text_from_completion(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    msg = first.get("message")
    if not isinstance(msg, dict):
        return ""
    return str(msg.get("content") or "").strip()


def _chat_completions_try_models(
    messages: List[Dict[str, str]],
    temperature: float,
    max_tokens: int,
    models: List[str],
) -> Tuple[str, Optional[str]]:
    """
    try each model in order until one returns 200 + non-empty assistant text.

    returns:
    - (reply_or_error_message, model_used_or_none).
    """
    if not models:
        return "OpenRouter: no models configured (empty OPENROUTER_FREE_MODELS).", None
    failures: List[str] = []
    for m in models:
        payload: Dict[str, Any] = {
            "model": m,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        try:
            resp = _openrouter_chat_once(payload)
            data = resp.json()
        except requests.HTTPError as exc:
            code = getattr(getattr(exc, "response", None), "status_code", "?")
            failures.append(f"{m}: HTTP {code}")
            continue
        except requests.RequestException as exc:
            failures.append(f"{m}: {exc!r}")
            continue
        except Exception as exc:
            failures.append(f"{m}: {exc!r}")
            continue
        try:
            text = _assistant_text_from_completion(data)
        except Exception as exc:
            failures.append(f"{m}: parse {exc!r}")
            continue
        if not text:
            failures.append(f"{m}: empty assistant content")
            continue
        print(f"[advisor] OpenRouter ok model={m!r}", flush=True)
        return text, m
    joined = " | ".join(failures) if failures else "unknown"
    return (
        "OpenRouter: all configured models failed. Details: "
        + joined
        + " (set OPENROUTER_MODEL to one working id, or wait if 429 rate limits.)",
        None,
    )


def openrouter_health_ping() -> tuple[bool, str]:
    """
    minimal API call (tiny completion) to verify key + model before a heavy advisor run.

    returns:
    - (true, reply text) on success
    - (false, error text) on failure
    """
    key = openrouter_api_key()
    if not key:
        return False, "missing OPENROUTER_API_KEY (or OPENROUTER / openrouter)"
    msgs = [{"role": "user", "content": "Reply with exactly one word: pong"}]
    models = openrouter_model_candidates()
    txt, used = _chat_completions_try_models(msgs, 0.1, 32, models)
    if used:
        return True, f"ok model={used!r} reply={txt!r}"
    return False, txt


def call_openrouter(user_question: str, context_blob: str) -> str:
    key = openrouter_api_key()
    if not key:
        return "OpenRouter API key missing (set OPENROUTER_API_KEY or OPENROUTER or openrouter in env)."

    user_block = (
        "=== CONTEXT LOAD REPORT ===\n"
        + build_context_load_report()
        + "\n\n=== USER QUESTION ===\n"
        + user_question.strip()
        + "\n\n=== REPO / RUNTIME CONTEXT (read-only) ===\n"
        + context_blob
    )
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_block},
    ]
    models = openrouter_model_candidates()
    text, _used = _chat_completions_try_models(messages, 0.35, 2048, models)
    return text


def _telegram_send_reply(text: str) -> None:
    from telegram_bot import TelegramBot

    bot = TelegramBot(
        token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
    if not bot.is_configured():
        return
    body = (text or "").strip() or "(empty reply)"
    bot.send_plain_by_lines("🧠 Advisor (OpenRouter)\n\n" + body)


def advisor_subprocess_entry(user_message: str) -> None:
    """runs in child process (separate from main bot)."""
    try:
        from dotenv import load_dotenv

        from config.constants import ENV_FILE

        if ENV_FILE.is_file():
            load_dotenv(ENV_FILE, override=False)
    except Exception:
        pass
    t0 = time.perf_counter()
    print(build_context_load_report(), flush=True)
    ctx = gather_advisor_context()
    print(f"[advisor] merged context chars={len(ctx)}", flush=True)
    answer = call_openrouter(user_message, ctx)
    _telegram_send_reply(answer)
    dt = time.perf_counter() - t0
    print(f"[advisor] done in {dt:.1f}s", flush=True)


def start_advisor_process(user_message: str) -> None:
    """spawn OS child process so OpenRouter work never blocks the trading loop."""
    msg = (user_message or "").strip()
    if not msg:
        return
    proc = multiprocessing.Process(
        target=advisor_subprocess_entry,
        args=(msg,),
        name="openrouter-advisor",
        daemon=True,
    )
    proc.start()


def _strip_ask_prefix(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    first = lines[0].strip()
    low = first.lower()
    if low.startswith("/ask"):
        rest = first[4:].lstrip(" \t@")
        tail = "\n".join(lines[1:])
        return (rest + ("\n" + tail if tail else "")).strip()
    return text.strip()


def should_route_message_to_advisor(raw_message: str) -> bool:
    if not advisor_enabled():
        return False
    text = (raw_message or "").strip()
    if not text:
        return False
    if text.lower().startswith("/ask"):
        return True
    if "BOT" in text:
        return True
    if re.search(r"\bbot\b", text, flags=re.IGNORECASE):
        return True
    if advisor_catchall_non_commands():
        head = normalize_telegram_command_text(text)
        if not head:
            return False
        first_tok = head.split()[0].split("@", 1)[0]
        if first_tok in _BUILTIN_FIRST_TOKENS:
            return False
        return True
    return False


def normalize_question_for_advisor(raw_message: str) -> str:
    return _strip_ask_prefix(raw_message)


def _cli_main() -> None:
    import sys

    try:
        from dotenv import load_dotenv

        from config.constants import ENV_FILE

        if ENV_FILE.is_file():
            load_dotenv(ENV_FILE, override=False)
    except Exception:
        pass

    argv = [a.strip() for a in sys.argv[1:] if a.strip()]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(
            "usage:\n"
            "  python -m notifications.openrouter_advisor ping\n"
            "      → one minimal OpenRouter request (no repo context); best key/rate-limit check\n"
            "  python -m notifications.openrouter_advisor test\n"
            "      → same as ping + print which context files exist\n"
            "  python -m notifications.openrouter_advisor context\n"
            "      → print context load report + merged char count (no API)\n"
            '  python -m notifications.openrouter_advisor ask "your question"\n'
            "      → full gather + OpenRouter (prints reply; no Telegram)\n"
            "\n"
            "env: OPENROUTER_API_KEY (or OPENROUTER / openrouter)\n"
            "     optional OPENROUTER_MODEL — if set, only that model is used (else free-model cascade)\n"
            "normal use: run the main bot (strategy/bot_runner.py); advisor is triggered from Telegram.",
            flush=True,
        )
        return
    if argv[0] == "ping":
        ok, msg = openrouter_health_ping()
        print(msg, flush=True)
        sys.exit(0 if ok else 1)
    if argv[0] == "test":
        ok, msg = openrouter_health_ping()
        print(msg, flush=True)
        print(build_context_load_report(), flush=True)
        sys.exit(0 if ok else 1)
    if argv[0] == "context":
        print(build_context_load_report(), flush=True)
        blob = gather_advisor_context()
        print(f"merged_context_chars={len(blob)}", flush=True)
        return
    if argv[0] == "ask":
        q = " ".join(argv[1:]).strip()
        if len(q) < 2:
            print(
                'usage: python -m notifications.openrouter_advisor ask "your question"',
                flush=True,
            )
            sys.exit(1)
        print(build_context_load_report(), flush=True)
        ctx = gather_advisor_context()
        print(f"merged_context_chars={len(ctx)}", flush=True)
        print("--- reply ---", flush=True)
        print(call_openrouter(q, ctx), flush=True)
        return
    print(f"unknown subcommand {argv[0]!r}; try --help", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    _cli_main()
