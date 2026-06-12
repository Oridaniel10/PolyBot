"""Build human-readable event summary: truth vs APIs vs Polymarket prices per city-day."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Any, DefaultDict, Dict, List, Optional, Set, Tuple

from config import constants as C
from forecast.parse_title import parse_highest_temp_title
from research.jsonl_util import iter_jsonl, save_json_file
from research.outcome_fields import (
    outcome_fits_truth,
    outcome_gamma_yes_p,
    outcome_question_snip,
)
from research.probability_from_forecast import implied_yes_prob


def _daterange(start: date, end: date) -> List[date]:
    out: List[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _parse_city_filter(raw: Optional[List[str]]) -> Optional[Set[str]]:
    if not raw:
        return None
    return {str(x).strip().lower() for x in raw if str(x).strip()}


def _latest_forecasts_for_date(
    target_ds: str,
) -> Dict[Tuple[str, str], Dict[str, float]]:
    """(city_key, event_date) -> {model: temp_max_c} keeping last row per model."""
    out: DefaultDict[Tuple[str, str], Dict[str, float]] = defaultdict(dict)
    for row in iter_jsonl(C.RESEARCH_FORECASTS_HISTORY_FILE):
        if str(row.get("event_date") or "") != target_ds:
            continue
        ck = str(row.get("city_key") or "").lower().strip()
        model = str(row.get("model") or "").strip()
        tv = row.get("temp_max_c")
        if not ck or not model or tv is None:
            continue
        try:
            out[(ck, target_ds)][model] = float(tv)
        except (TypeError, ValueError):
            continue
    return dict(out)


def _truth_for_date(target_ds: str) -> Dict[str, float]:
    m: Dict[str, float] = {}
    for row in iter_jsonl(C.RESEARCH_TRUTH_DAILY_FILE):
        if str(row.get("event_date") or "") != target_ds:
            continue
        ck = str(row.get("city_key") or "").lower().strip()
        tv = row.get("truth_max_c")
        if not ck or tv is None:
            continue
        try:
            m[ck] = float(tv)
        except (TypeError, ValueError):
            continue
    return m


def _latest_outcomes_for_date(target_ds: str) -> Dict[str, Dict[str, Any]]:
    """market_id -> latest row (by scan order = last in file wins)."""
    best: Dict[str, Dict[str, Any]] = {}
    for row in iter_jsonl(C.RESEARCH_MARKET_OUTCOMES_FILE):
        if str(row.get("event_date") or "") != target_ds:
            continue
        mid = str(row.get("market_id") or "").strip()
        if not mid:
            continue
        best[mid] = row
    return best


def _group_outcomes_by_event(
    outcomes: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    by_e: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in outcomes.values():
        eid = str(row.get("gamma_event_id") or "").strip()
        if eid:
            by_e[eid].append(row)
    return dict(by_e)


def build_event_summary(
    target_ds: str,
    city_filter: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    truth_by_city = _truth_for_date(target_ds)
    fc_by_cd = _latest_forecasts_for_date(target_ds)
    outcomes = _latest_outcomes_for_date(target_ds)
    by_event = _group_outcomes_by_event(outcomes)

    events_out: List[Dict[str, Any]] = []
    for eid, rows in sorted(by_event.items(), key=lambda x: x[0]):
        ck = str(rows[0].get("city_key") or "").lower().strip()
        if city_filter is not None and ck not in city_filter:
            continue
        truth_c = truth_by_city.get(ck)
        fc_map = fc_by_cd.get((ck, target_ds), {})
        om_f = fc_map.get("open_meteo_forecast")
        om_h = fc_map.get("open_meteo_historical_forecast")

        parsed_list: List[Any] = []
        market_rows: List[Dict[str, Any]] = []
        for r in rows:
            q = outcome_question_snip(r, 500)
            p = parse_highest_temp_title(q)
            yp = outcome_gamma_yes_p(r)
            if p:
                parsed_list.append(p)
            model_p_om: Optional[float] = None
            model_p_hist: Optional[float] = None
            if p and om_f is not None:
                model_p_om = implied_yes_prob(p, float(om_f))
            if p and om_h is not None:
                model_p_hist = implied_yes_prob(p, float(om_h))
            market_rows.append(
                {
                    "market_id": r.get("market_id"),
                    "gamma_yes_p": yp,
                    "fits_truth": outcome_fits_truth(r),
                    "model_p_open_meteo_forecast": model_p_om,
                    "model_p_open_meteo_historical_forecast": model_p_hist,
                    "q_snip": q[:180],
                }
            )

        leader = None
        if market_rows:
            leader = max(
                market_rows,
                key=lambda x: float(x.get("gamma_yes_p") or 0),
            )

        events_out.append(
            {
                "gamma_event_id": eid,
                "city_key": ck,
                "event_date": target_ds,
                "truth_max_c": truth_c,
                "truth_present": truth_c is not None,
                "open_meteo_forecast_max_c": om_f,
                "open_meteo_historical_forecast_max_c": om_h,
                "forecast_minus_truth": (
                    (float(om_f) - float(truth_c))
                    if (om_f is not None and truth_c is not None)
                    else None
                ),
                "crowd_leader_market_id": leader.get("market_id") if leader else None,
                "crowd_leader_gamma_yes_p": leader.get("gamma_yes_p")
                if leader
                else None,
                "crowd_leader_fits_truth": leader.get("fits_truth") if leader else None,
                "markets": market_rows,
            }
        )

    cities_truth = sorted(truth_by_city.keys())
    if city_filter is not None:
        cities_truth = sorted(c for c in cities_truth if c in city_filter)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_date": target_ds,
        "n_events": len(events_out),
        "cities_with_truth": cities_truth,
        "events": events_out,
    }


def build_event_summary_range(
    start_d: date,
    end_d: date,
    city_filter: Optional[Set[str]],
) -> Dict[str, Any]:
    daily: List[Dict[str, Any]] = []
    for d in _daterange(start_d, end_d):
        ds = d.isoformat()
        daily.append(build_event_summary(ds, city_filter=city_filter))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_date_start": start_d.isoformat(),
        "event_date_end": end_d.isoformat(),
        "city_filter": sorted(city_filter) if city_filter is not None else None,
        "n_days": len(daily),
        "daily_summaries": daily,
    }


def _flatten_range_to_csv_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for day in payload.get("daily_summaries") or []:
        ds = str(day.get("event_date") or "")
        for ev in day.get("events") or []:
            ck = str(ev.get("city_key") or "")
            base = {
                "event_date": ds,
                "city_key": ck,
                "gamma_event_id": ev.get("gamma_event_id"),
                "truth_max_c": ev.get("truth_max_c"),
                "truth_present": ev.get("truth_present"),
                "open_meteo_forecast_max_c": ev.get("open_meteo_forecast_max_c"),
                "open_meteo_historical_forecast_max_c": ev.get(
                    "open_meteo_historical_forecast_max_c"
                ),
                "forecast_minus_truth": ev.get("forecast_minus_truth"),
                "crowd_leader_market_id": ev.get("crowd_leader_market_id"),
                "crowd_leader_gamma_yes_p": ev.get("crowd_leader_gamma_yes_p"),
                "crowd_leader_fits_truth": ev.get("crowd_leader_fits_truth"),
            }
            for m in ev.get("markets") or []:
                r = dict(base)
                r["market_id"] = m.get("market_id")
                r["gamma_yes_p"] = m.get("gamma_yes_p")
                r["fits_truth"] = m.get("fits_truth")
                r["model_p_open_meteo_forecast"] = m.get("model_p_open_meteo_forecast")
                r["model_p_open_meteo_historical_forecast"] = m.get(
                    "model_p_open_meteo_historical_forecast"
                )
                r["q_snip"] = m.get("q_snip")
                rows.append(r)
    return rows


def _range_export_paths(
    start_d: date,
    end_d: date,
    city_filter: Optional[Set[str]],
) -> Tuple[str, Path, Path]:
    C.RESEARCH_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    suf = f"{start_d.isoformat()}_{end_d.isoformat()}"
    if city_filter:
        slug = "-".join(sorted(c.replace(" ", "_") for c in city_filter))[:120]
        suf = f"{suf}_cities_{slug}"
    base = f"event_summary_range_{suf}"
    return (
        base,
        C.RESEARCH_EXPORTS_DIR / f"{base}.json",
        C.RESEARCH_EXPORTS_DIR / f"{base}.csv",
    )


def run_event_summary(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Summarize research JSONL for one event date or a date range (optional city filter)",
    )
    ap.add_argument(
        "--date",
        type=str,
        default="",
        help="single event_date YYYY-MM-DD",
    )
    ap.add_argument(
        "--start-date", type=str, default="", help="range start (use with --end-date)"
    )
    ap.add_argument("--end-date", type=str, default="", help="range end inclusive")
    ap.add_argument(
        "--city",
        action="append",
        default=None,
        metavar="CITY",
        help="filter by city key (repeatable), e.g. --city 'tel aviv' --city 'new york city'",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print JSON only, do not write files",
    )
    args = ap.parse_args(argv)
    city_filter = _parse_city_filter(args.city)
    has_single = bool(str(args.date).strip())
    has_range = bool(str(args.start_date).strip() and str(args.end_date).strip())
    if has_single and has_range:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "use either --date or --start-date/--end-date, not both",
                }
            ),
            file=sys.stderr,
        )
        return 2
    if not has_single and not has_range:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "need --date YYYY-MM-DD or --start-date and --end-date",
                }
            ),
            file=sys.stderr,
        )
        return 2

    if has_range:
        start_d = date.fromisoformat(str(args.start_date).strip())
        end_d = date.fromisoformat(str(args.end_date).strip())
        if start_d > end_d:
            start_d, end_d = end_d, start_d
        summary = build_event_summary_range(start_d, end_d, city_filter)
        _, path_json, path_csv = _range_export_paths(start_d, end_d, city_filter)
        txt = json.dumps(summary, indent=2, ensure_ascii=True) + "\n"
        flat = _flatten_range_to_csv_rows(summary)
        if args.dry_run:
            print(txt)
            return 0
        C.RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        path_json.write_text(txt, encoding="utf-8")
        save_json_file(C.RESEARCH_EVENT_SUMMARY_FILE, summary)
        if flat:
            with path_csv.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(flat[0].keys()))
                w.writeheader()
                for row in flat:
                    w.writerow(row)
        else:
            path_csv.write_text("event_date,city_key,note\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "json": str(path_json),
                    "csv": str(path_csv),
                    "n_days": len(summary.get("daily_summaries") or []),
                },
                indent=2,
            )
        )
        return 0

    ds = str(args.date).strip()
    summary = build_event_summary(ds, city_filter=city_filter)
    path_dated = C.RESEARCH_DIR / f"event_summary_{ds}.json"
    txt = json.dumps(summary, indent=2, ensure_ascii=True) + "\n"
    if args.dry_run:
        print(txt)
        return 0
    C.RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    path_dated.write_text(txt, encoding="utf-8")
    save_json_file(C.RESEARCH_EVENT_SUMMARY_FILE, summary)
    _, _, path_csv = _range_export_paths(
        date.fromisoformat(ds), date.fromisoformat(ds), city_filter
    )
    flat = _flatten_range_to_csv_rows({"daily_summaries": [summary], "event_date": ds})
    if flat:
        with path_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(flat[0].keys()))
            w.writeheader()
            for row in flat:
                w.writerow(row)
    else:
        path_csv.write_text("event_date,city_key,note\n", encoding="utf-8")
    print(
        json.dumps(
            {"ok": True, "json": str(path_dated), "csv": str(path_csv)}, indent=2
        )
    )
    print(txt)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(run_event_summary(sys.argv[1:]))
