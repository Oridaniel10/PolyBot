"""Build Telegram HTML digest: Open-Meteo (+ optional OW) daily max per city-day (~every N min)."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import os
import threading
import time
from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Set, Tuple

from config.constants import ENV_FILE
from config.settings import get_effective_settings
from polymarket_client import PolymarketClient, PolymarketConfig
from state.store import load_env_file, read_state
from strategy.time_utils import build_target_day_label, format_report_local_hhmm, now_in_report_timezone
from telegram_bot import TelegramBot, tg_escape

from forecast.cache_store import save_forecast_cache_after_gamma_fetch
from forecast.forecast_service import get_forecast_max_for_city_day
from forecast.parse_title import parse_highest_temp_title

# US °F markets sort late alphabetically (e.g. nyc); boost after held-priority groups
_US_DIGEST_FIRST_CITIES = frozenset(
    {
        "atlanta",
        "austin",
        "chicago",
        "dallas",
        "denver",
        "houston",
        "los angeles",
        "miami",
        "new york",
        "new york city",
        "nyc",
        "san francisco",
        "seattle",
    }
)


def _digest_max_groups(settings: Any) -> int:
    from config import constants as C

    v = int(
        getattr(
            settings,
            "forecast_digest_max_location_groups",
            C.FORECAST_DIGEST_MAX_LOCATION_GROUPS,
        )
    )
    return max(4, min(int(C.FORECAST_DIGEST_MAX_GROUPS_CAP), v))


def _group_markets(
    markets: List[Dict[str, Any]],
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for m in markets:
        title = str(m.get("question") or m.get("title") or "")
        p = parse_highest_temp_title(title)
        if not p:
            continue
        key = (p.city_key, p.event_date.isoformat())
        out[key].append(m)
    return out


def _markets_for_digest_scope(
    markets: List[Dict[str, Any]], settings: Any
) -> Tuple[str, List[Dict[str, Any]]]:
    scope = str(getattr(settings, "forecast_digest_scope", "scan") or "scan").lower()
    if scope == "positions":
        st = read_state()
        held: Set[str] = set(st.get("active_trades") or {})
        markets = [m for m in markets if str(m.get("id") or "") in held]
    return scope, markets


def _digest_group_rows(
    markets: List[Dict[str, Any]],
    settings: Any,
    ow_key: str,
    *,
    full_city_day_coverage: bool = False,
) -> List[Dict[str, Any]]:
    from config import constants as C

    groups = _group_markets(markets)
    rows: List[Dict[str, Any]] = []
    st = read_state()
    held_ids = {
        str(k).strip()
        for k in (st.get("active_trades") or {}).keys()
        if str(k).strip()
    }
    sorted_items = sorted(groups.items(), key=lambda x: (x[0][0], x[0][1]))
    priority: List[Tuple[Tuple[str, str], List[Dict[str, Any]]]] = []
    rest: List[Tuple[Tuple[str, str], List[Dict[str, Any]]]] = []
    for key, mks in sorted_items:
        mids = {str(m.get("id") or "").strip() for m in mks if str(m.get("id") or "").strip()}
        if held_ids and mids & held_ids:
            priority.append((key, mks))
        else:
            rest.append((key, mks))
    rest.sort(
        key=lambda item: (
            0 if item[0][0] in _US_DIGEST_FIRST_CITIES else 1,
            item[0][0],
            item[0][1],
        )
    )
    n_pri = len(priority)
    if full_city_day_coverage:
        group_items = priority + rest
    else:
        max_groups = _digest_max_groups(settings)
        cap = int(C.FORECAST_DIGEST_MAX_GROUPS_CAP)
        effective_cap = min(cap, max(max_groups, n_pri))
        spill = max(0, effective_cap - n_pri)
        group_items = priority + rest[:spill]
    for (city_key, date_iso), mks in group_items:
        p0 = parse_highest_temp_title(
            str(mks[0].get("question") or mks[0].get("title") or "")
        )
        if not p0:
            continue
        om, ow, cons = get_forecast_max_for_city_day(
            city_key,
            p0.event_date,
            p0.tz_name,
            openweather_api_key=ow_key,
        )
        member_ids = sorted(
            {
                str(m.get("id") or "").strip()
                for m in mks
                if str(m.get("id") or "").strip()
            }
        )
        rows.append(
            {
                "city_key": city_key,
                "date_iso": date_iso,
                "timezone_label": p0.tz_name.split("/")[-1],
                "open_meteo_c": om,
                "openweather_c": ow,
                "consensus_c": cons,
                "member_market_ids": member_ids,
                "markets": [],
            }
        )
    return rows


def build_forecast_preview_payload(
    markets: List[Dict[str, Any]],
    settings: Any,
    *,
    full_city_day_coverage: bool = False,
) -> Dict[str, Any]:
    n_gamma = len(markets)
    scope, scoped = _markets_for_digest_scope(markets, settings)
    grouped_all = _group_markets(scoped)
    parsable = 0
    for m in scoped:
        t = str(m.get("question") or m.get("title") or "")
        if parse_highest_temp_title(t):
            parsable += 1
    ow_enabled = bool(getattr(settings, "enable_openweather_forecast", False))
    ow_key = os.getenv("OPENWEATHER_API_KEY", "").strip() if ow_enabled else ""
    groups = _digest_group_rows(
        scoped, settings, ow_key, full_city_day_coverage=full_city_day_coverage
    )
    cap_applied = (
        len(grouped_all) if full_city_day_coverage else _digest_max_groups(settings)
    )
    return {
        "generated_at_local": format_report_local_hhmm(),
        "scope": scope,
        "openweather_enabled": ow_enabled,
        "openweather_key_configured": bool(ow_key),
        "group_count": len(groups),
        "groups": groups,
        "digest_meta": {
            "gamma_markets_fetched": n_gamma,
            "markets_in_scope": len(scoped),
            "markets_parsable_temp_title": parsable,
            "city_day_groups_total": len(grouped_all),
            "city_day_groups_included": len(groups),
            "digest_max_groups_applied": cap_applied,
        },
    }


def build_forecast_digest_html(
    markets: List[Dict[str, Any]],
    settings: Any,
    *,
    full_city_day_coverage: bool = False,
) -> str:
    scope, scoped = _markets_for_digest_scope(markets, settings)
    groups = _group_markets(scoped)
    if not groups:
        return (
            f"📡 <b>Forecast digest</b>  <code>{tg_escape(format_report_local_hhmm())}</code>\n"
            "<i>No parsable temperature markets in this batch.</i>"
        )

    ow_enabled = bool(getattr(settings, "enable_openweather_forecast", False))
    ow_key = (
        os.getenv("OPENWEATHER_API_KEY", "").strip()
        if ow_enabled
        else ""
    )

    rows = _digest_group_rows(
        scoped, settings, ow_key, full_city_day_coverage=full_city_day_coverage
    )
    total_groups = len(groups)

    lines: List[str] = [
        f"📡 <b>Forecast digest</b>  <code>{tg_escape(format_report_local_hhmm())}</code>",
        f"<i>scope={tg_escape(scope)} · Open-Meteo + "
        f"({'OW' if ow_key else 'no'}) OpenWeather"
        f"{' · <b>all city-days</b>' if full_city_day_coverage else ''}</i>",
        "",
    ]

    for row in rows:
        city_key = row["city_key"]
        date_iso = row["date_iso"]
        om = row["open_meteo_c"]
        ow = row["openweather_c"]
        cons = row["consensus_c"]
        fc_parts = []
        if om is not None:
            fc_parts.append(f"OM <code>{om:.1f}</code>°C")
        if ow is not None:
            fc_parts.append(f"OW <code>{ow:.1f}</code>°C")
        if cons is not None:
            fc_parts.append(f"consensus <code>{cons:.1f}</code>°C")
        fc_s = " · ".join(fc_parts) if fc_parts else "no forecast"

        lines.append(
            f"<b>{tg_escape(city_key.title())}</b>  {tg_escape(date_iso)}  ·  {fc_s}"
        )

        lines.append(f"<i>{tg_escape(row['timezone_label'])} local</i>")
        lines.append("")

    if len(rows) < total_groups:
        lines.append(
            f"<i>…and {total_groups - len(rows)} more city-day group(s) omitted "
            f"(cap {_digest_max_groups(settings)}).</i>"
        )

    lines.append(
        "<i>Open-Meteo daily max (+ optional OpenWeather). "
        "Informative unless forecast_gate_buy is on.</i>"
    )
    return "\n".join(lines)


def run_forecast_digest_once(
    client: PolymarketClient,
    telegram: TelegramBot,
    *,
    standalone: bool = False,
    log_print: bool = False,
    force: bool = False,
    send_telegram: bool = True,
) -> bool:
    """
    Fetches markets, writes forecast_cache.json, optionally sends digest HTML.
    returns True if a Telegram digest was sent.
    """
    settings = get_effective_settings()
    if not standalone and not force and not getattr(settings, "forecast_digest_enabled", True):
        return False
    now = now_in_report_timezone()
    label = build_target_day_label(now)
    prev = build_target_day_label(now - timedelta(days=1))
    extra = [prev] if prev != label else []
    anchor_ids: List[int] = []
    try:
        markets = client.get_highest_temperature_markets(
            target_day_label=label,
            extra_target_day_labels=extra,
            anchor_ids=anchor_ids,
        )
    except Exception as exc:
        if telegram.is_configured():
            telegram.send_html_chunks(
                f"📡 <b>Forecast digest failed</b>\n<pre>{tg_escape(repr(exc))}</pre>"
            )
        if log_print:
            print(f"forecast: Gamma fetch failed: {exc!r}", flush=True)
        return False

    full_digest = bool(force)
    preview = build_forecast_preview_payload(
        markets, settings, full_city_day_coverage=full_digest
    )
    preview["target_day_label"] = label
    preview["secondary_target_day_label"] = prev if extra else None
    preview["markets_scanned"] = len(markets)
    preview["error"] = None
    wrote = False
    try:
        wrote = save_forecast_cache_after_gamma_fetch(
            preview, gamma_market_count=len(markets)
        )
    except OSError as exc:
        if log_print:
            print(f"forecast: cache write failed: {exc!r}", flush=True)

    if log_print:
        if wrote:
            print(
                f"forecast: wrote cache — {len(markets)} markets, "
                f"{preview.get('group_count', 0)} groups → data/forecast_cache.json",
                flush=True,
            )
        elif not markets:
            print(
                "forecast: 0 markets from gamma — kept existing forecast_cache.json",
                flush=True,
            )

    if send_telegram and telegram.is_configured():
        html = build_forecast_digest_html(
            markets, settings, full_city_day_coverage=full_digest
        )
        telegram.send_html_chunks(html)
        return True
    if send_telegram and log_print and not telegram.is_configured():
        print(
            "forecast: telegram not configured — HTML digest not sent "
            "(set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)",
            flush=True,
        )
    return False


def forecast_digest_loop_runner(
    telegram: TelegramBot,
    cfg: PolymarketConfig,
    *,
    standalone: bool = False,
) -> None:
    load_env_file(ENV_FILE)
    client = PolymarketClient(cfg)
    if standalone:
        print("forecast: first refresh now, then on interval…", flush=True)
    last_tg_mono = time.monotonic()
    while True:
        try:
            eff = get_effective_settings()
            refresh_sec = int(
                getattr(
                    eff,
                    "forecast_digest_refresh_interval_sec",
                    120,
                )
            )
            refresh_sec = max(60, min(3600, refresh_sec))
            tg_iv = int(getattr(eff, "forecast_digest_telegram_interval_sec", 0))
            tg_iv = max(0, min(86400, tg_iv))
        except Exception:
            refresh_sec = 120
            tg_iv = 0

        now_m = time.monotonic()
        if tg_iv <= 0:
            send_digest = False
        else:
            send_digest = telegram.is_configured() and (now_m - last_tg_mono) >= tg_iv

        try:
            eff = get_effective_settings()
            if standalone or getattr(eff, "forecast_digest_enabled", True):
                sent = run_forecast_digest_once(
                    client,
                    telegram,
                    standalone=standalone,
                    log_print=standalone,
                    send_telegram=send_digest,
                )
                if sent:
                    last_tg_mono = time.monotonic()
        except Exception as exc:
            try:
                if telegram.is_configured():
                    telegram.send_html_chunks(
                        f"📡 <b>Forecast digest error</b>\n<pre>{tg_escape(repr(exc))}</pre>"
                    )
            except Exception:
                pass
        time.sleep(refresh_sec)


def start_forecast_digest_background(
    telegram: TelegramBot, cfg: PolymarketConfig
) -> None:
    t = threading.Thread(
        target=forecast_digest_loop_runner,
        args=(telegram, cfg),
        name="forecast-digest",
        daemon=True,
    )
    t.start()


if __name__ == "__main__":
    raise SystemExit(
        "run standalone digest from repo root:\n"
        "  python -m forecast\n"
        "(not: python forecast/digest_runner.py)"
    )
