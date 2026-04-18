import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parent.parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from config import constants as C
from forecast.cache_store import (
    forecast_cache_age_sec,
    load_forecast_cache,
    save_forecast_cache_after_gamma_fetch,
)
from config.settings import (
    default_runtime_dict,
    get_effective_settings,
    load_blacklist_day_ids,
    load_runtime_config_file,
    save_blacklist_day_file,
    save_runtime_config_file,
)
from notifications.dashboard_notify import notify_dashboard_change
from polymarket_client import PolymarketClient, condition_ids_equivalent
from state.pnl_ledger import pnl_summary_day_week
from state.store import read_state
from forecast.digest_runner import build_forecast_preview_payload
from strategy.bot_runner import build_config
from strategy.time_utils import build_target_day_label, now_in_report_timezone

dashboard_router = APIRouter(prefix="/api", tags=["dashboard"])


class RuntimeConfigUpdate(BaseModel):
    buy_min_threshold: Optional[float] = Field(None, ge=0.0, le=0.99)
    buy_max_threshold: Optional[float] = Field(None, ge=0.01, le=0.99)
    buy_disable_price_band: Optional[bool] = None
    stop_loss_threshold: Optional[float] = Field(None, ge=0.01, le=0.99)
    stop_loss_use_entry_tiers: Optional[bool] = None
    stop_loss_tier_entry_split: Optional[float] = Field(None, ge=0.05, le=0.95)
    stop_loss_tier_mark_low: Optional[float] = Field(None, ge=0.01, le=0.99)
    stop_loss_tier_mark_high: Optional[float] = Field(None, ge=0.01, le=0.99)
    take_profit_threshold: Optional[float] = Field(None, ge=0.01, le=0.99)
    min_lead_over_runner_up: Optional[float] = Field(None, ge=0.0, le=0.95)
    min_lead_momentum_over_runner_up: Optional[float] = Field(None, ge=0.0, le=0.95)
    enable_competition_filter: Optional[bool] = None
    enable_momentum: Optional[bool] = None
    momentum_window_min: Optional[int] = Field(None, ge=1, le=1440)
    momentum_rise: Optional[float] = Field(None, ge=0.0, le=5.0)
    momentum_min_price: Optional[float] = Field(None, ge=0.01, le=0.99)
    momentum_max_entry: Optional[float] = Field(None, ge=0.01, le=0.99)
    momentum_peer_drop: Optional[float] = Field(None, ge=0.0, le=1.0)
    churn_max_stop_cycles: Optional[int] = Field(None, ge=1, le=20)
    churn_cooldown_sec: Optional[int] = Field(None, ge=60, le=86400)
    blacklist_market_ids: Optional[List[str]] = None
    scan_interval_seconds: Optional[int] = Field(None, ge=5, le=600)
    min_order_notional_usd: Optional[float] = Field(None, ge=0.0, le=1_000_000)
    max_buy_notional_usd: Optional[float] = Field(None, ge=0.0, le=1_000_000)
    max_trade_fraction_of_cash: Optional[float] = Field(None, ge=0.0, le=1.0)
    dashboard_weather_max_pages: Optional[int] = Field(None, ge=1, le=80)
    cash_reserve_usd: Optional[float] = Field(None, ge=0.0, le=1_000_000)
    forecast_digest_enabled: Optional[bool] = None
    forecast_digest_refresh_interval_sec: Optional[int] = Field(None, ge=60, le=3600)
    forecast_digest_telegram_interval_sec: Optional[int] = Field(None, ge=0, le=86400)
    forecast_digest_interval_sec: Optional[int] = Field(None, ge=120, le=3600)
    forecast_digest_scope: Optional[str] = None
    forecast_digest_max_location_groups: Optional[int] = Field(None, ge=4, le=500)
    enable_openweather_forecast: Optional[bool] = None
    enable_flow_sampling: Optional[bool] = None
    flow_max_events_per_tick: Optional[int] = Field(None, ge=1, le=40)
    enable_flow_peer_exit: Optional[bool] = None
    flow_peer_window_sec: Optional[int] = Field(None, ge=120, le=3600)
    flow_peer_surge_drop: Optional[float] = Field(None, ge=0.0, le=1.0)
    flow_peer_surge_rise: Optional[float] = Field(None, ge=0.0, le=1.0)
    forecast_gate_buy: Optional[bool] = None
    forecast_contradict_margin_c: Optional[float] = Field(None, ge=0.0, le=15.0)
    forecast_exact_bucket_support_slack_c: Optional[float] = Field(
        None, ge=0.0, le=15.0
    )
    forecast_reduce_usd_if_weak: Optional[bool] = None
    forecast_weak_size_factor: Optional[float] = Field(None, ge=0.05, le=1.0)
    research_exit_on_model_flip: Optional[bool] = None
    research_edge_gate_buy: Optional[bool] = None
    research_min_edge: Optional[float] = Field(None, ge=0.0, le=0.5)
    research_min_edge_after_fees_add: Optional[float] = Field(None, ge=0.0, le=0.5)
    research_sigma_c: Optional[float] = Field(None, ge=0.0, le=8.0)
    research_weather_taker_fee_rate: Optional[float] = Field(None, ge=0.0, le=0.2)
    research_edge_implied_soft_floor: Optional[float] = Field(None, ge=0.0, le=0.95)
    research_edge_implied_soft_boost: Optional[float] = Field(None, ge=0.0, le=3.0)
    buy_earliest_local_hour: Optional[int] = Field(None, ge=0, le=23)
    buy_latest_local_hour: Optional[int] = Field(None, ge=0, le=24)
    research_edge_scale_size: Optional[bool] = None
    research_edge_size_slope: Optional[float] = Field(None, ge=0.0, le=50.0)
    research_edge_size_cap_mult: Optional[float] = Field(None, ge=1.0, le=3.0)
    research_crowd_soft_match: Optional[bool] = None
    research_crowd_soft_band: Optional[float] = Field(None, ge=0.0, le=0.5)
    research_crowd_soft_edge_factor: Optional[float] = Field(None, ge=0.1, le=1.0)
    research_crowd_disagree_gap: Optional[float] = Field(None, ge=0.0, le=0.5)
    research_crowd_disagree_extra_edge: Optional[float] = Field(None, ge=0.0, le=0.5)
    research_skip_telegram_cooldown_sec: Optional[int] = Field(None, ge=0, le=86400)
    decision_skip_telegram_notify: Optional[bool] = None
    max_market_prob_for_buy: Optional[float] = Field(None, ge=0.05, le=0.99)
    min_model_prob_for_buy: Optional[float] = Field(None, ge=0.0, le=0.95)
    max_positions_per_event: Optional[int] = Field(None, ge=1, le=20)
    decision_min_model_peak_prob: Optional[float] = Field(None, ge=0.02, le=0.5)
    enable_peer_surge_exit: Optional[bool] = None
    peer_surge_window_min: Optional[int] = Field(None, ge=1, le=180)
    peer_surge_rise_threshold: Optional[float] = Field(None, ge=0.01, le=2.0)
    peer_surge_event_cooldown_sec: Optional[int] = Field(None, ge=0, le=86400)
    peer_surge_skip_buy_enabled: Optional[bool] = None
    peer_surge_skip_buy_rise_threshold: Optional[float] = Field(None, ge=0.01, le=2.0)


class BlacklistToggle(BaseModel):
    market_id: str = Field(..., min_length=1)
    enabled: bool = True


@dashboard_router.get("/pnl/summary")
def api_pnl_summary() -> Dict[str, Any]:
    return pnl_summary_day_week()


@dashboard_router.get("/portfolio/positions")
def api_portfolio_positions() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "cash_usd": 0.0,
        "positions_mtm_usd": 0.0,
        "total_usd": 0.0,
        "rows": [],
        "error": None,
    }
    try:
        client = PolymarketClient(build_config())
        bal = client.get_portfolio_balance(force_allowance_refresh=False)
        out["cash_usd"] = round(float(bal.get("cash") or 0), 2)
        out["positions_mtm_usd"] = round(
            float(bal.get("positions_market_value") or 0), 2
        )
        out["total_usd"] = round(float(bal.get("total_value") or 0), 2)
        positions = client.get_open_positions()
        tracked = read_state().get("active_trades", {})
        rows: List[Dict[str, Any]] = []
        for p in positions:
            mid = str(p.get("market_id") or p.get("marketId") or "").strip()
            cid = str(p.get("condition_id") or "").strip()
            bot_tracked = bool(mid and mid in tracked)
            if not bot_tracked and cid:
                for tv in tracked.values():
                    tc = str(tv.get("condition_id") or "")
                    if tc and condition_ids_equivalent(tc, cid):
                        bot_tracked = True
                        break
            sz = float(p.get("size") or 0)
            cur = float(p.get("cur_price") or 0)
            avg = float(p.get("avg_price") or 0)
            cv = float(p.get("current_value") or 0)
            val = cv if cv > 1e-6 else cur * sz
            rows.append(
                {
                    "market_id": mid or None,
                    "condition_id": cid or None,
                    "title": str(p.get("title") or ""),
                    "outcome": str(p.get("outcome") or ""),
                    "shares": round(sz, 6),
                    "avg_price": round(avg, 6),
                    "cur_price": round(cur, 6),
                    "cash_pnl": round(float(p.get("cash_pnl") or 0), 2),
                    "value_usd": round(val, 2),
                    "bot_tracked": bot_tracked,
                }
            )
        out["rows"] = rows
    except Exception as exc:
        out["error"] = repr(exc)
    return out


@dashboard_router.get("/runtime-config")
def api_get_runtime_config() -> Dict[str, Any]:
    base = default_runtime_dict()
    merged = load_runtime_config_file()
    eff = get_effective_settings()
    return {
        "runtime": {**base, **{k: merged[k] for k in base if k in merged}},
        "blacklist_day_ids": sorted(load_blacklist_day_ids()),
        "effective_blacklist": sorted(eff.blacklist_market_ids),
    }


@dashboard_router.post("/runtime-config")
def api_post_runtime_config(body: RuntimeConfigUpdate) -> Dict[str, Any]:
    cur = load_runtime_config_file()
    patch = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "buy_min_threshold" in patch and "buy_max_threshold" in patch:
        if patch["buy_min_threshold"] > patch["buy_max_threshold"]:
            patch["buy_min_threshold"], patch["buy_max_threshold"] = (
                patch["buy_max_threshold"],
                patch["buy_min_threshold"],
            )
    min_n = patch.get("min_order_notional_usd")
    max_b = patch.get("max_buy_notional_usd")
    if min_n is not None and max_b is not None and min_n > max_b:
        raise HTTPException(
            status_code=400,
            detail="min_order_notional_usd cannot exceed max_buy_notional_usd",
        )
    changed_lines: List[str] = []
    for key, new_value in patch.items():
        old_value = cur.get(key)
        if old_value != new_value:
            changed_lines.append(f"✅ {key}: {old_value!r} -> {new_value!r}")
    cur.update(patch)
    save_runtime_config_file(cur)
    if changed_lines:
        notify_dashboard_change("Runtime config updated (dashboard)", changed_lines)
    return {"ok": True, "runtime": load_runtime_config_file()}


@dashboard_router.post("/runtime-config/reset")
def api_reset_runtime_config() -> Dict[str, Any]:
    defaults = default_runtime_dict()
    cur = load_runtime_config_file()
    changed_lines: List[str] = []
    for key in defaults:
        old_value = cur.get(key)
        new_value = defaults[key]
        if old_value != new_value:
            changed_lines.append(f"🔄 {key}: {old_value!r} -> {new_value!r}")
    save_runtime_config_file(defaults)
    if changed_lines:
        notify_dashboard_change(
            "Runtime config RESET to defaults (dashboard)", changed_lines
        )
    return {
        "ok": True,
        "runtime": load_runtime_config_file(),
        "changes": len(changed_lines),
    }


@dashboard_router.post("/blacklist/toggle")
def api_blacklist_toggle(body: BlacklistToggle) -> Dict[str, Any]:
    mid = body.market_id.strip()
    ids = set(load_blacklist_day_ids())
    if body.enabled:
        ids.add(mid)
    else:
        ids.discard(mid)
    save_blacklist_day_file(sorted(ids))
    notify_dashboard_change(
        "Blacklist updated (dashboard)",
        [f"market_id={mid}", f"enabled={body.enabled}", f"today_ids_count={len(ids)}"],
    )
    return {"ok": True, "blacklist_day_ids": sorted(ids)}


@dashboard_router.get("/markets/weather-today")
def api_weather_markets_today(
    limit: int = Query(200, ge=1, le=500),
    max_pages: Optional[int] = Query(
        None,
        ge=1,
        le=80,
        description="Gamma pages to scan (default from runtime dashboard_weather_max_pages)",
    ),
) -> Dict[str, Any]:
    now = now_in_report_timezone()
    label = build_target_day_label(now)
    prev_label = build_target_day_label(now - timedelta(days=1))
    extra = [prev_label] if prev_label != label else []
    eff = get_effective_settings()
    mp = max_pages if max_pages is not None else eff.dashboard_weather_max_pages
    try:
        client = PolymarketClient(build_config())
        markets = client.get_highest_temperature_markets(
            target_day_label=label,
            extra_target_day_labels=extra,
            max_pages=mp,
        )
    except Exception as exc:
        return {
            "target_day_label": label,
            "secondary_target_day_label": prev_label if extra else None,
            "error": repr(exc),
            "markets": [],
            "total_matched": 0,
            "showing": 0,
            "max_pages_used": mp,
        }
    total = len(markets)
    markets = markets[:limit]
    slim: List[Dict[str, Any]] = []
    for m in markets:
        slim.append(
            {
                "id": str(m.get("id") or ""),
                "question": m.get("question") or m.get("title") or "",
                "lastTradePrice": m.get("lastTradePrice"),
                "probability": m.get("probability"),
            }
        )
    return {
        "target_day_label": label,
        "secondary_target_day_label": prev_label if extra else None,
        "markets": slim,
        "total_matched": total,
        "showing": len(slim),
        "max_pages_used": mp,
        "error": None,
    }


@dashboard_router.get("/forecast/preview")
def api_forecast_preview(
    max_pages: Optional[int] = Query(
        None,
        ge=1,
        le=80,
        description="Gamma pages (default from runtime dashboard_weather_max_pages)",
    ),
    refresh: bool = Query(
        False,
        description="if true, bypass data/forecast_cache.json and refetch",
    ),
) -> Dict[str, Any]:
    now = now_in_report_timezone()
    label = build_target_day_label(now)
    prev_label = build_target_day_label(now - timedelta(days=1))
    extra = [prev_label] if prev_label != label else []
    eff = get_effective_settings()
    mp = max_pages if max_pages is not None else eff.dashboard_weather_max_pages

    if not refresh:
        cached = load_forecast_cache()
        age = forecast_cache_age_sec()
        cg = cached.get("groups") if isinstance(cached, dict) else None
        cache_has_groups = isinstance(cg, list) and len(cg) > 0
        if (
            isinstance(cached, dict)
            and cached.get("groups") is not None
            and age is not None
            and age < float(C.FORECAST_CACHE_MAX_AGE_SEC)
            and cache_has_groups
        ):
            out = dict(cached)
            out["from_cache"] = True
            out["cache_age_sec"] = round(age, 1)
            out.setdefault("error", None)
            return out

    try:
        client = PolymarketClient(build_config())
        markets = client.get_highest_temperature_markets(
            target_day_label=label,
            extra_target_day_labels=extra,
            max_pages=mp,
        )
    except Exception as exc:
        stale = load_forecast_cache()
        if isinstance(stale, dict) and stale.get("groups"):
            out = dict(stale)
            out["from_cache"] = True
            out["stale_after_error"] = True
            out["error"] = repr(exc)
            out["cache_age_sec"] = round(forecast_cache_age_sec() or 0.0, 1)
            return out
        return {
            "error": repr(exc),
            "target_day_label": label,
            "max_pages_used": mp,
        }
    if not markets:
        stale = load_forecast_cache()
        sg = stale.get("groups") if isinstance(stale, dict) else None
        if isinstance(stale, dict) and isinstance(sg, list) and len(sg) > 0:
            out = dict(stale)
            out["from_cache"] = True
            out["stale_after_error"] = True
            out["error"] = (
                "Live fetch returned 0 highest-temperature markets; showing cached snapshot."
            )
            out["cache_age_sec"] = round(forecast_cache_age_sec() or 0.0, 1)
            return out
    preview = build_forecast_preview_payload(markets, eff)
    preview["target_day_label"] = label
    preview["secondary_target_day_label"] = prev_label if extra else None
    preview["max_pages_used"] = mp
    preview["markets_scanned"] = len(markets)
    preview["error"] = None
    preview["from_cache"] = False
    try:
        save_forecast_cache_after_gamma_fetch(preview, gamma_market_count=len(markets))
    except OSError:
        pass
    if not markets and not (preview.get("groups") or []):
        preview["error"] = (
            "Live fetch returned 0 highest-temperature markets and no usable cache on disk."
        )
    return preview


@dashboard_router.get("/trades/today")
def api_trades_today() -> List[Dict[str, Any]]:
    from state.pnl_ledger import read_trade_csv_today

    return read_trade_csv_today()


@dashboard_router.get("/trades/today/csv")
def api_trades_today_csv():
    from fastapi.responses import FileResponse
    from state.pnl_ledger import trade_csv_path_today

    path = trade_csv_path_today()
    if not path.is_file():
        raise HTTPException(404, "no trades today")
    return FileResponse(path, media_type="text/csv", filename=path.name)


@dashboard_router.get("/trades/history")
def api_trades_history(
    date: str = Query(..., description="YYYY-MM-DD"),
) -> List[Dict[str, Any]]:
    import csv as csv_mod
    path = C.DATA_DIR / f"trade_log_{date}.csv"
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            return list(csv_mod.DictReader(f))
    except OSError:
        return []


@dashboard_router.get("/trades/dates")
def api_trades_dates() -> List[str]:
    dates: List[str] = []
    for p in sorted(C.DATA_DIR.glob("trade_log_*.csv")):
        stem = p.stem.replace("trade_log_", "")
        if stem and not stem.endswith("_legacy"):
            dates.append(stem)
    return dates


@dashboard_router.get("/decisions/recent")
def api_decisions_recent(
    limit: int = Query(50, ge=1, le=200),
) -> List[Dict[str, Any]]:
    from strategy.decision_core import get_recent_decisions
    return get_recent_decisions(limit)


@dashboard_router.get("/stats/summary")
def api_stats_summary(
    date: str = Query(..., description="YYYY-MM-DD"),
) -> Dict[str, Any]:
    import csv as csv_mod
    path = C.DATA_DIR / f"trade_log_{date}.csv"
    if not path.is_file():
        return {"date": date, "error": "no_data"}
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv_mod.DictReader(f))
    except OSError:
        return {"date": date, "error": "read_error"}

    buys = 0
    sells = 0
    wins = 0
    losses = 0
    total_pnl = 0.0
    biggest_win = 0.0
    biggest_loss = 0.0
    total_buy_usd = 0.0
    total_sell_usd = 0.0
    for r in rows:
        action = str(r.get("action") or "").upper()
        pnl = float(r.get("pnl_usd") or 0)
        usd = float(r.get("usd") or 0)
        if action == "BUY":
            buys += 1
            total_buy_usd += usd
        elif action in ("SELL", "LIMIT_SELL"):
            sells += 1
            total_sell_usd += usd
            total_pnl += pnl
            if pnl > 0:
                wins += 1
                biggest_win = max(biggest_win, pnl)
            elif pnl < 0:
                losses += 1
                biggest_loss = min(biggest_loss, pnl)
    win_rate = wins / max(1, wins + losses)
    return {
        "date": date,
        "total_trades": len(rows),
        "buys": buys,
        "sells": sells,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "biggest_win": round(biggest_win, 2),
        "biggest_loss": round(biggest_loss, 2),
        "total_buy_usd": round(total_buy_usd, 2),
        "total_sell_usd": round(total_sell_usd, 2),
    }


@dashboard_router.get("/health")
def api_health() -> Dict[str, str]:
    return {"status": "ok"}


def create_dashboard_app():
    """
    FastAPI app: /api/* + static UI at /dashboard/ — without the trading bot thread.
    """
    import os

    from dotenv import load_dotenv
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    from fastapi.staticfiles import StaticFiles

    load_dotenv(C.ENV_FILE, override=True)
    app = FastAPI(title="Polymarket dashboard (API + UI, no bot)")
    app.include_router(dashboard_router)
    if C.UI_DIST_DIR.is_dir():
        app.mount(
            "/dashboard",
            StaticFiles(directory=str(C.UI_DIST_DIR), html=True),
            name="dashboard",
        )

    @app.get("/", response_class=PlainTextResponse)
    def root() -> str:
        p = os.environ.get("PORT", "8080")
        return (
            "Polymarket dashboard server.\n"
            f"  UI:  http://127.0.0.1:{p}/dashboard/\n"
            f"  API: http://127.0.0.1:{p}/api/health\n"
            "Bot + same UI/API: python main_bot.py\n"
        )

    return app


if __name__ == "__main__":
    import os

    import uvicorn

    _port = int(os.environ.get("PORT", "8081"))
    print(
        f"Starting dashboard only (no bot). Open http://127.0.0.1:{_port}/dashboard/\n"
        f"Build UI first if missing: cd ui && npm install && npm run build\n",
        flush=True,
    )
    uvicorn.run(create_dashboard_app(), host="0.0.0.0", port=_port)
