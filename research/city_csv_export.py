"""Per-city CSV tables: Open-Meteo forecasts vs truth (Poly + OM archive) from JSONL."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config import constants as C
from research.cities_catalog import load_research_city_keys
from research.jsonl_util import iter_jsonl


def _daterange(start: date, end: date) -> List[date]:
    out: List[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _truth_sources_scan() -> Tuple[
    Dict[Tuple[str, str], float],
    Dict[Tuple[str, str], float],
]:
    """returns (polymarket_truth_by_cd, open_meteo_archive_truth_by_cd) last row per bucket."""
    poly: Dict[Tuple[str, str], float] = {}
    om_arc: Dict[Tuple[str, str], float] = {}
    for row in iter_jsonl(C.RESEARCH_TRUTH_DAILY_FILE):
        ck = str(row.get("city_key") or "").strip().lower()
        ds = str(row.get("event_date") or "").strip()
        tv = row.get("truth_max_c")
        if not ck or not ds or tv is None:
            continue
        try:
            v = float(tv)
        except (TypeError, ValueError):
            continue
        src = str(row.get("source") or row.get("truth_source") or "").strip().lower()
        k = (ck, ds)
        if src == "polymarket":
            poly[k] = v
        if src == "open_meteo_archive":
            om_arc[k] = v
    return poly, om_arc


def _forecast_scan() -> Dict[Tuple[str, str], Dict[str, float]]:
    """(city, date) -> {model: temp_max_c} last wins."""
    out: Dict[Tuple[str, str], Dict[str, float]] = {}
    for row in iter_jsonl(C.RESEARCH_FORECASTS_HISTORY_FILE):
        ck = str(row.get("city_key") or "").strip().lower()
        ds = str(row.get("event_date") or "").strip()
        model = str(row.get("model") or "").strip()
        tv = row.get("temp_max_c")
        if not ck or not ds or not model or tv is None:
            continue
        try:
            v = float(tv)
        except (TypeError, ValueError):
            continue
        k = (ck, ds)
        out.setdefault(k, {})[model] = v
    return out


def _date_bounds_from_data() -> Tuple[Optional[date], Optional[date]]:
    ds_min: Optional[str] = None
    ds_max: Optional[str] = None
    for row in iter_jsonl(C.RESEARCH_FORECASTS_HISTORY_FILE):
        ds = str(row.get("event_date") or "").strip()
        if not ds:
            continue
        if ds_min is None or ds < ds_min:
            ds_min = ds
        if ds_max is None or ds > ds_max:
            ds_max = ds
    if not ds_min or not ds_max:
        return None, None
    return date.fromisoformat(ds_min), date.fromisoformat(ds_max)


def write_city_csv(
    city_key: str,
    start_d: date,
    end_d: date,
    poly: Dict[Tuple[str, str], float],
    om_arc: Dict[Tuple[str, str], float],
    fc: Dict[Tuple[str, str], Dict[str, float]],
    out_dir: Path,
) -> Path:
    ck = city_key.strip().lower()
    slug = ck.replace(" ", "_").replace("/", "-")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}.csv"
    rows: List[Dict[str, Any]] = []
    for d in _daterange(start_d, end_d):
        ds = d.isoformat()
        k = (ck, ds)
        fmap = fc.get(k, {})
        rows.append(
            {
                "event_date": ds,
                "open_meteo_forecast_max_c": fmap.get("open_meteo_forecast", ""),
                "open_meteo_historical_forecast_max_c": fmap.get(
                    "open_meteo_historical_forecast", ""
                ),
                "truth_polymarket_max_c": poly.get(k, ""),
                "truth_open_meteo_archive_max_c": om_arc.get(k, ""),
            }
        )
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "event_date",
                "open_meteo_forecast_max_c",
                "open_meteo_historical_forecast_max_c",
                "truth_polymarket_max_c",
                "truth_open_meteo_archive_max_c",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def write_all_city_csvs(
    start_d: date,
    end_d: date,
    *,
    cities: Optional[Iterable[str]] = None,
    out_dir: Optional[Path] = None,
) -> List[Path]:
    poly, om_arc = _truth_sources_scan()
    fc = _forecast_scan()
    out_dir = out_dir or C.RESEARCH_CITY_TABLES_DIR
    city_list = (
        [c.strip().lower() for c in cities] if cities else load_research_city_keys()
    )
    written: List[Path] = []
    for ck in city_list:
        written.append(write_city_csv(ck, start_d, end_d, poly, om_arc, fc, out_dir))
    return written


def run_export_cities_main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Write per-city CSV tables under data/research/city_tables/"
    )
    ap.add_argument(
        "--start-date",
        type=str,
        default="",
        help="YYYY-MM-DD (default: min date in forecasts JSONL)",
    )
    ap.add_argument(
        "--end-date",
        type=str,
        default="",
        help="YYYY-MM-DD (default: max date in forecasts JSONL)",
    )
    args = ap.parse_args(argv)
    if (args.start_date or "").strip() and (args.end_date or "").strip():
        start_d = date.fromisoformat(str(args.start_date).strip())
        end_d = date.fromisoformat(str(args.end_date).strip())
    else:
        lo, hi = _date_bounds_from_data()
        if lo is None or hi is None:
            print(json.dumps({"ok": False, "error": "no_forecasts_jsonl_or_empty"}))
            return 1
        start_d, end_d = lo, hi
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    paths = write_all_city_csvs(start_d, end_d)
    print(
        json.dumps(
            {
                "ok": True,
                "start_date": start_d.isoformat(),
                "end_date": end_d.isoformat(),
                "n_files": len(paths),
                "out_dir": str(C.RESEARCH_CITY_TABLES_DIR),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(run_export_cities_main(sys.argv[1:]))
