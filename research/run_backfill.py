"""CLI: backfill truth, forecasts, outcomes into data/research/*.jsonl."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from config import constants as C
from forecast.geocode import city_lat_lon
from forecast.parse_title import parse_highest_temp_title
from polymarket_client import PolymarketClient, PolymarketConfig
from research.bracket_truth import yes_bucket_matches_truth
from research.cities_catalog import (
    load_research_city_keys,
    verify_all_cities_have_coordinates,
)
from research.gamma_research import (
    iter_highest_temp_with_event_ids,
    write_registry_from_markets,
)
from research.jsonl_util import (
    append_jsonl,
    ensure_research_dir,
    iter_jsonl,
    load_forecast_idempotency_success,
    load_truth_city_dates_with_success,
    rewrite_jsonl_excluding,
    save_json_file,
)
from research.open_meteo_research import (
    fetch_archive_daily_max_c,
    fetch_daily_max_with_retries,
    fetch_forecast_daily_max_c,
    fetch_historical_forecast_daily_max_c,
)
from research.truth_polymarket import (
    group_parsed_rows_by_city_date,
    truth_max_c_from_polymarket_event,
)
from state.store import load_env_file
from strategy.city_tz import CITY_TZ


def _yes_price_from_gamma(market: Dict[str, Any]) -> Optional[float]:
    op = market.get("outcomePrices")
    if isinstance(op, str):
        try:
            op = json.loads(op)
        except json.JSONDecodeError:
            return None
    if isinstance(op, list) and op:
        try:
            return float(op[0])
        except (TypeError, ValueError):
            return None
    return None


def _daterange(start: date, end: date) -> List[date]:
    out: List[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _build_truth_map_for_keys(
    path,
    keys: Set[Tuple[str, str]],
    overlay: Dict[Tuple[str, str], float],
) -> Dict[Tuple[str, str], float]:
    m: Dict[Tuple[str, str], float] = {}
    for row in iter_jsonl(path):
        ck = str(row.get("city_key") or "").lower().strip()
        ds = str(row.get("event_date") or "").strip()
        if (ck, ds) not in keys:
            continue
        tv = row.get("truth_max_c")
        if tv is None:
            continue
        try:
            m[(ck, ds)] = float(tv)
        except (TypeError, ValueError):
            pass
    m.update(overlay)
    return m


def run_backfill_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Research backfill → data/research/")
    parser.add_argument("--days", type=int, default=14, help="event_date window length")
    parser.add_argument(
        "--start-date", type=str, default="", help="inclusive lower event_date"
    )
    parser.add_argument(
        "--end-date", type=str, default="", help="inclusive upper event_date"
    )
    parser.add_argument(
        "--back-days",
        type=int,
        default=0,
        metavar="N",
        help="if N>=1, set inclusive window to last N calendar days ending at --end-date or today (overrides --start-date and --days)",
    )
    parser.add_argument(
        "--max-markets", type=int, default=400, help="cap markets (after parse)"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="parallel HTTP workers for truth + forecasts (thread pool)",
    )
    parser.add_argument(
        "--truth-sleep",
        type=float,
        default=0.0,
        help="optional extra sleep sec before each Open-Meteo call (sequential mode only; use 0 with --workers)",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="sequential truth/forecasts (slow; for debugging)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=80,
        help="print progress every N completed forecast fetches (0=disable)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--quick-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--search-pages",
        type=int,
        default=16,
        help="Gamma public-search pages (max 40)",
    )
    parser.add_argument(
        "--skip-truth", action="store_true", help="skip writing truth_daily"
    )
    parser.add_argument("--refresh-outcomes", action="store_true")
    parser.add_argument(
        "--forecasts-markets-only",
        action="store_true",
        help="forecast only (city,date) seen in parsed markets; default = full cities.json × date grid",
    )
    parser.add_argument(
        "--allow-missing-city-coords",
        action="store_true",
        help="do not exit when cities.json has missing lat/lon or CITY_TZ (not recommended)",
    )
    parser.add_argument(
        "--cached",
        action="store_true",
        help="log cache-friendly mode (Open-Meteo/Poly skips when rows already on disk; same as default idempotency)",
    )
    parser.add_argument(
        "--skip-gamma-search",
        action="store_true",
        help="do not call Gamma search API; no new markets/outcomes this run (only truth+forecasts grid)",
    )
    parser.add_argument(
        "--no-city-csv-export",
        action="store_true",
        help="skip writing data/research/city_tables/*.csv at end",
    )
    args = parser.parse_args(argv)
    if args.refresh_outcomes:
        block = os.getenv("RESEARCH_BLOCK_REFRESH_OUTCOMES", "").strip().lower()
        if block in ("1", "true", "yes", "on"):
            print(
                "[research] ERROR: --refresh-outcomes blocked because "
                "RESEARCH_BLOCK_REFRESH_OUTCOMES is set (e.g. CI safety). "
                "Unset it to allow rewriting market_outcomes.jsonl.",
                file=sys.stderr,
            )
            return 1
    if args.quick_test:
        args.search_pages = 4
        args.max_markets = min(args.max_markets, 40)
        args.workers = min(args.workers, 3)
        args.truth_sleep = 0.02
        args.verbose = True

    def log(msg: str) -> None:
        if args.verbose:
            print(msg, flush=True)

    def progress(msg: str) -> None:
        """always on — short heartbeat for long runs (works without -v)."""
        print(msg, flush=True)

    ensure_research_dir()
    if not C.RESEARCH_RESOLUTION_OVERRIDES_FILE.is_file():
        save_json_file(C.RESEARCH_RESOLUTION_OVERRIDES_FILE, {})

    canonical_cities = load_research_city_keys()
    coord_problems = verify_all_cities_have_coordinates(canonical_cities)
    if coord_problems and not args.allow_missing_city_coords:
        for p in coord_problems:
            log(f"[research] CRITICAL: {p}")
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "city_catalog_incomplete",
                    "problems": coord_problems,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    load_env_file(C.ENV_FILE)
    cfg = PolymarketConfig(
        api_key=os.getenv("API_KEY", ""),
        api_secret=os.getenv("API_SECRET", ""),
        api_passphrase=os.getenv("API_PASSPHRASE", ""),
        private_key=os.getenv("POLY_PRIVATE_KEY", ""),
        proxy_address=os.getenv("POLY_PROXY_ADDRESS", "")
        or os.getenv("POLY_ADDRESS", ""),
        gamma_base_url=os.getenv(
            "POLY_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"
        ),
        clob_base_url=os.getenv("POLY_CLOB_BASE_URL", "https://clob.polymarket.com"),
    )
    client = PolymarketClient(cfg)

    pairs: List[Tuple[str, Dict[str, Any]]] = []
    if args.skip_gamma_search:
        progress(
            "[research] skip-gamma-search: no Gamma HTTP; outcomes/registry unchanged this run"
        )
    else:
        log("[research] fetching Gamma public-search (highest temperature)…")
        t_gamma = time.monotonic()
        pairs = list(
            iter_highest_temp_with_event_ids(
                client,
                max_pages=args.search_pages,
                include_inactive=True,
                verbose=args.verbose,
            )
        )
        log(
            f"[research] gamma done: {len(pairs)} market rows in {time.monotonic() - t_gamma:.1f}s"
        )
        if not args.dry_run:
            write_registry_from_markets(pairs)
            log("[research] wrote resolution_registry.json (schema v2)")

    back_days = max(0, int(getattr(args, "back_days", 0) or 0))
    if back_days >= 1:
        if (args.end_date or "").strip():
            end_d = date.fromisoformat(str(args.end_date).strip())
        else:
            end_d = date.today()
        start_d = end_d - timedelta(days=back_days - 1)
    else:
        if (args.end_date or "").strip():
            end_d = date.fromisoformat(str(args.end_date).strip())
        else:
            end_d = date.today()
        if (args.start_date or "").strip():
            start_d = date.fromisoformat(str(args.start_date).strip())
        else:
            start_d = end_d - timedelta(days=max(1, int(args.days)))
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    date_list = _daterange(start_d, end_d)
    bd_note = f" (--back-days={back_days})" if back_days >= 1 else ""
    log(
        f"[research] event_date window {start_d.isoformat()} … {end_d.isoformat()}{bd_note} "
        f"({len(date_list)} days); cities={len(canonical_cities)}"
    )

    parsed_rows: List[Dict[str, Any]] = []
    for eid, m in pairs:
        title = str(m.get("question") or m.get("title") or "")
        p = parse_highest_temp_title(title)
        if not p:
            continue
        if p.event_date < start_d or p.event_date > end_d:
            continue
        cid = str(
            m.get("conditionId") or m.get("condition_id") or m.get("questionID") or ""
        ).strip()
        parsed_rows.append(
            {
                "market": m,
                "parsed": p,
                "event_id": eid,
                "condition_id": cid,
            }
        )

    parsed_rows = parsed_rows[: max(1, args.max_markets)]
    log(f"[research] parsed {len(parsed_rows)} markets in window")

    truth_overlay: Dict[Tuple[str, str], float] = {}
    truth_success = load_truth_city_dates_with_success(C.RESEARCH_TRUTH_DAILY_FILE)
    idem_fc_ok = load_forecast_idempotency_success(C.RESEARCH_FORECASTS_HISTORY_FILE)
    if args.cached:
        progress(
            f"[research] cached: on disk already — truth pairs={len(truth_success)}, "
            f"forecast ok keys={len(idem_fc_ok)}"
        )

    if args.refresh_outcomes:
        strip_mids = {str(row["market"].get("id") or "").strip() for row in parsed_rows}
        strip_mids.discard("")
        if strip_mids:
            rm = rewrite_jsonl_excluding(
                C.RESEARCH_MARKET_OUTCOMES_FILE,
                lambda r: str(r.get("market_id") or "").strip() in strip_mids,
            )
            if rm and args.verbose:
                log(f"[research] refresh-outcomes: removed {rm} stale outcome rows")
    else:
        progress(
            "[research] market_outcomes: append-only — existing rows kept "
            "(only --refresh-outcomes rewrites overlapping market_ids)"
        )

    from research.jsonl_util import load_idempotent_keys

    idem_out = load_idempotent_keys(
        C.RESEARCH_MARKET_OUTCOMES_FILE, lambda r: r.get("idempotency_key")
    )

    today_utc = datetime.now(timezone.utc).date()
    poly_by_cd = group_parsed_rows_by_city_date(parsed_rows)
    inferred_poly: Dict[Tuple[str, str], float] = {}
    for kcd, rows in poly_by_cd.items():
        v = truth_max_c_from_polymarket_event(rows)
        if v is not None:
            inferred_poly[kcd] = v

    truth_lock = threading.Lock()
    fc_lock = threading.Lock()

    def process_one_truth(
        job: Tuple[Any, ...],
    ) -> Optional[Tuple[Tuple[str, str], float]]:
        tag = job[0]
        if tag == "poly":
            _, ck, ds, tval = job
            src = "polymarket"
        else:
            _, ck, ds, event_d, lat, lon, tz_name = job
            if args.no_parallel:
                time.sleep(max(0.0, float(args.truth_sleep)))
            tval = fetch_daily_max_with_retries(
                fetch_archive_daily_max_c,
                lat=lat,
                lon=lon,
                target=event_d,
                timezone=tz_name,
            )
            src = "open_meteo_archive"
        rec = {
            "city_key": ck,
            "event_date": ds,
            "truth_max_c": tval,
            "source": src,
        }
        with truth_lock:
            append_jsonl(C.RESEARCH_TRUTH_DAILY_FILE, rec)
        if args.verbose:
            log(f"[research] truth {ck} {ds} src={src} max_c={tval}")
        if tval is not None:
            return (ck, ds), float(tval)
        return None

    truth_jobs: List[Tuple[Any, ...]] = []
    if not args.dry_run and not args.skip_truth:
        for ck in canonical_cities:
            ck = ck.strip().lower()
            tz_name = CITY_TZ.get(ck)
            ll = city_lat_lon(ck)
            if not tz_name or not ll:
                continue
            lat, lon = ll[0], ll[1]
            for event_d in date_list:
                if event_d > today_utc:
                    continue
                ds = event_d.isoformat()
                kcd = (ck, ds)
                if kcd in truth_success:
                    continue
                if kcd in inferred_poly:
                    truth_jobs.append(("poly", ck, ds, inferred_poly[kcd]))
                else:
                    truth_jobs.append(("arc", ck, ds, event_d, lat, lon, tz_name))

    if args.dry_run:
        log("[research] dry-run: skipping truth + forecast HTTP")
    elif not args.skip_truth:
        n_t = len(truth_jobs)
        progress(
            f"[research] truth: {n_t} jobs "
            f"(parallel={not args.no_parallel}, workers={max(1, min(args.workers, 24))})"
        )
        t_truth0 = time.monotonic()
        prog_step = int(args.progress_every) if int(args.progress_every) > 0 else 0
        if args.no_parallel:
            for i, job in enumerate(truth_jobs, start=1):
                r = process_one_truth(job)
                if r:
                    kcd2, tv = r
                    truth_overlay[kcd2] = tv
                    truth_success.add(kcd2)
                if prog_step and i % prog_step == 0 and n_t:
                    progress(f"[research] truth {i}/{n_t} ({100 * i / n_t:.0f}%) …")
        else:
            w = max(1, min(int(args.workers), 24))
            done_t = 0
            with ThreadPoolExecutor(max_workers=w) as ex:
                futs = [ex.submit(process_one_truth, j) for j in truth_jobs]
                for fut in as_completed(futs):
                    r = fut.result()
                    done_t += 1
                    if r:
                        kcd2, tv = r
                        truth_overlay[kcd2] = tv
                        truth_success.add(kcd2)
                    if prog_step and done_t % prog_step == 0 and n_t:
                        progress(
                            f"[research] truth {done_t}/{n_t} ({100 * done_t / n_t:.0f}%) …"
                        )
        if n_t:
            progress(
                f"[research] truth finished {n_t} jobs in {time.monotonic() - t_truth0:.1f}s"
            )

    forecast_cells: List[Tuple[str, str, date]] = []
    if args.forecasts_markets_only:
        seen: Set[Tuple[str, str]] = set()
        for row in parsed_rows:
            p = row["parsed"]
            k = (p.city_key.lower(), p.event_date.isoformat())
            if k not in seen:
                seen.add(k)
                forecast_cells.append((p.city_key.lower(), k[1], p.event_date))
    else:
        for ck in canonical_cities:
            ck = ck.strip().lower()
            for event_d in date_list:
                forecast_cells.append((ck, event_d.isoformat(), event_d))

    models = (
        ("open_meteo_forecast", fetch_forecast_daily_max_c),
        ("open_meteo_historical_forecast", fetch_historical_forecast_daily_max_c),
    )
    expected_fc = len(forecast_cells) * len(models)
    fetch_failures: List[List[str]] = []

    def process_one_forecast(
        job: Tuple[str, str, date, str, Any, float, float, str],
    ) -> Optional[List[str]]:
        ck, ds, event_d, model_name, fn, lat, lon, tz_name = job
        ikey = f"fc|{model_name}|{ck}|{ds}"
        if args.no_parallel:
            time.sleep(max(0.0, float(args.truth_sleep)))
        t0 = time.monotonic()
        val = fetch_daily_max_with_retries(
            fn, lat=lat, lon=lon, target=event_d, timezone=tz_name
        )
        rec = {
            "idempotency_key": ikey,
            "model": model_name,
            "city_key": ck,
            "event_date": ds,
            "tz": tz_name,
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "temp_max_c": val,
            "latency_sec": round(time.monotonic() - t0, 3),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        with fc_lock:
            append_jsonl(C.RESEARCH_FORECASTS_HISTORY_FILE, rec)
            if val is not None:
                idem_fc_ok.add(ikey)
        if args.verbose:
            log(f"[research] forecast {ck} {ds} {model_name} temp_max_c={val}")
        if val is None:
            return [ck, ds, model_name, "open_meteo_null_after_retries"]
        return None

    fc_jobs: List[Tuple[str, str, date, str, Any, float, float, str]] = []
    if not args.dry_run:
        for ck, ds, event_d in forecast_cells:
            tz_name = CITY_TZ.get(ck)
            ll = city_lat_lon(ck)
            if not tz_name or not ll:
                for model_name, _fn in models:
                    fetch_failures.append([ck, ds, model_name, "no_tz_or_coords"])
                continue
            lat, lon = ll[0], ll[1]
            for model_name, fn in models:
                ikey = f"fc|{model_name}|{ck}|{ds}"
                if ikey in idem_fc_ok:
                    continue
                fc_jobs.append((ck, ds, event_d, model_name, fn, lat, lon, tz_name))

    if not args.dry_run:
        n_fc = len(fc_jobs)
        log(
            f"[research] forecasts: expected={expected_fc} rows, "
            f"queued_http={n_fc} (skipped idem + bad coords)"
        )
        progress(
            f"[research] forecasts: {n_fc} HTTP fetches "
            f"(parallel={not args.no_parallel}, workers={max(1, min(args.workers, 24))})"
        )
        prog_step = int(args.progress_every) if int(args.progress_every) > 0 else 0
        t_fc0 = time.monotonic()
        if args.no_parallel:
            for i, job in enumerate(fc_jobs, start=1):
                fail = process_one_forecast(job)
                if fail:
                    fetch_failures.append(fail)
                if prog_step and i % prog_step == 0 and n_fc:
                    progress(
                        f"[research] forecasts {i}/{n_fc} ({100 * i / n_fc:.0f}%) …"
                    )
        else:
            w = max(1, min(int(args.workers), 24))
            done = 0
            with ThreadPoolExecutor(max_workers=w) as ex:
                futs = [ex.submit(process_one_forecast, j) for j in fc_jobs]
                for fut in as_completed(futs):
                    fail = fut.result()
                    done += 1
                    if fail:
                        fetch_failures.append(fail)
                    if prog_step and done % prog_step == 0 and n_fc:
                        progress(
                            f"[research] forecasts {done}/{n_fc} ({100 * done / n_fc:.0f}%) …"
                        )
        if n_fc:
            progress(
                f"[research] forecasts finished {n_fc} fetches in {time.monotonic() - t_fc0:.1f}s"
            )

    still_missing: List[List[str]] = []
    if not args.dry_run and forecast_cells:
        idem_reload = load_forecast_idempotency_success(
            C.RESEARCH_FORECASTS_HISTORY_FILE
        )
        for ck, ds, _event_d in forecast_cells:
            ck = ck.strip().lower()
            for model_name, _fn in models:
                ikey = f"fc|{model_name}|{ck}|{ds}"
                if ikey not in idem_reload:
                    still_missing.append([ck, ds, model_name])

    fc_report = {
        "expected_forecast_rows": expected_fc,
        "forecast_cells": len(forecast_cells),
        "fetch_failures_this_run": fetch_failures,
        "still_missing_success_keys": still_missing,
        "complete": None if args.dry_run else (len(still_missing) == 0),
    }
    if not args.dry_run:
        log(
            f"[research] forecast report: expected={expected_fc} "
            f"missing_ok={len(still_missing)} failures_logged={len(fetch_failures)}"
        )

    needed_cd = {
        (row["parsed"].city_key.lower(), row["parsed"].event_date.isoformat())
        for row in parsed_rows
    }
    truth_by_cd = _build_truth_map_for_keys(
        C.RESEARCH_TRUTH_DAILY_FILE, needed_cd, truth_overlay
    )

    progress(f"[research] outcomes: {len(parsed_rows)} markets (idem skips existing)…")
    log(f"[research] writing outcomes for {len(parsed_rows)} markets…")
    oc_done = 0
    oc_prog = int(args.progress_every) if int(args.progress_every) > 0 else 0
    for row in parsed_rows:
        m = row["market"]
        p = row["parsed"]
        mid = str(m.get("id") or "").strip()
        ikey = f"outcome|{mid}"
        if ikey in idem_out:
            continue
        oc_done += 1
        if args.verbose and oc_done % 25 == 1:
            log(f"[research] outcomes progress {oc_done} new rows…")
        elif oc_prog and oc_done > 0 and oc_done % oc_prog == 0:
            progress(f"[research] outcomes {oc_done} new rows written…")
        yp = _yes_price_from_gamma(m)
        status = str(m.get("closed") or m.get("status") or "").lower()
        closed = bool(m.get("closed")) or status in ("closed", "resolved", "claimable")

        ck = p.city_key.lower()
        ds = p.event_date.isoformat()
        truth_c = truth_by_cd.get((ck, ds))
        yes_fit: Optional[bool] = None
        if truth_c is not None:
            try:
                yes_fit = yes_bucket_matches_truth(p, float(truth_c))
            except Exception:
                yes_fit = None

        q_raw = str(m.get("question") or "")
        q_max = int(C.RESEARCH_OUTCOME_QUESTION_MAX_LEN)
        rec = {
            "idempotency_key": ikey,
            "market_id": mid,
            "condition_id": row.get("condition_id") or "",
            "gamma_event_id": row.get("event_id") or "",
            "city_key": ck,
            "event_date": ds,
            "q": q_raw[:q_max],
            "gamma_yes_p": yp,
            "closed": closed,
            "truth_max_c": truth_c,
            "fits": yes_fit,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        if not args.dry_run:
            append_jsonl(C.RESEARCH_MARKET_OUTCOMES_FILE, rec)
            idem_out.add(ikey)

    w_cap = max(1, min(int(args.workers), 24))
    run_rec: Dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "markets_seen": len(pairs),
        "parsed_in_window": len(parsed_rows),
        "dry_run": bool(args.dry_run),
        "days": int(args.days),
        "event_date_start": start_d.isoformat(),
        "event_date_end": end_d.isoformat(),
        "back_days": int(back_days) if back_days >= 1 else 0,
        "forecast_report": fc_report,
        "http_parallel": bool(not args.dry_run and not args.no_parallel),
        "http_workers": w_cap
        if (not args.dry_run and not args.no_parallel)
        else (1 if not args.dry_run else 0),
        "progress_every": int(args.progress_every),
        "cached_mode": bool(args.cached),
        "skip_gamma_search": bool(args.skip_gamma_search),
        "city_csv_export": not bool(args.no_city_csv_export),
    }
    if not args.dry_run:
        append_jsonl(C.RESEARCH_DAILY_RUNS_FILE, run_rec)

    if not args.dry_run and not args.no_city_csv_export:
        from research.city_csv_export import write_all_city_csvs

        write_all_city_csvs(start_d, end_d)
        progress(
            f"[research] city CSV tables: {C.RESEARCH_CITY_TABLES_DIR} "
            f"({start_d.isoformat()} … {end_d.isoformat()})"
        )

    print(
        json.dumps(
            {
                "ok": True,
                "registry_written": not args.dry_run and not args.skip_gamma_search,
                "parsed_markets": len(parsed_rows),
                "run": run_rec,
                "city_csv_dir": str(C.RESEARCH_CITY_TABLES_DIR)
                if (not args.dry_run and not args.no_city_csv_export)
                else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_backfill_main())
