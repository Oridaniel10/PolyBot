"""Read data/research/*.jsonl → data/research/calibration_latest.json."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, DefaultDict, Dict, List, Optional, Set, Tuple

from config import constants as C
from research.jsonl_util import iter_jsonl, save_json_file
from research.outcome_fields import outcome_fits_truth, outcome_gamma_yes_p


def build_truth_map(
    city_filter: Optional[Set[str]] = None,
    ds_gte: Optional[str] = None,
    ds_lte: Optional[str] = None,
) -> Dict[Tuple[str, str], float]:
    m: Dict[Tuple[str, str], float] = {}
    for row in iter_jsonl(C.RESEARCH_TRUTH_DAILY_FILE):
        ck = str(row.get("city_key") or "").strip().lower()
        ds = str(row.get("event_date") or "").strip()
        tv = row.get("truth_max_c")
        if not ck or not ds or tv is None:
            continue
        if city_filter is not None and ck not in city_filter:
            continue
        if ds_gte and ds < ds_gte:
            continue
        if ds_lte and ds > ds_lte:
            continue
        try:
            m[(ck, ds)] = float(tv)
        except (TypeError, ValueError):
            continue
    return m


def analyze_forecast_errors(
    truth: Dict[Tuple[str, str], float],
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, List[float]]]]:
    errs_by_model: DefaultDict[str, List[float]] = defaultdict(list)
    errs_by_city_model: DefaultDict[str, DefaultDict[str, List[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    # last non-null row wins per (city, date, model) — avoids duplicate jsonl lines
    fc_last: Dict[Tuple[str, str, str], float] = {}
    for row in iter_jsonl(C.RESEARCH_FORECASTS_HISTORY_FILE):
        model = str(row.get("model") or "").strip()
        ck = str(row.get("city_key") or "").strip().lower()
        ds = str(row.get("event_date") or "").strip()
        fc = row.get("temp_max_c")
        if not model or not ck or not ds or fc is None:
            continue
        try:
            fc_last[(ck, ds, model)] = float(fc)
        except (TypeError, ValueError):
            continue
    for (ck, ds, model), fc in fc_last.items():
        k = (ck, ds)
        if k not in truth:
            continue
        err = fc - truth[k]
        errs_by_model[model].append(err)
        errs_by_city_model[ck][model].append(err)

    models_out: Dict[str, Any] = {}
    for model, arr in errs_by_model.items():
        if not arr:
            continue
        n = len(arr)
        bias = sum(arr) / n
        mae = sum(abs(x) for x in arr) / n
        models_out[model] = {"bias_c": round(bias, 4), "mae_c": round(mae, 4), "n": n}

    by_city: Dict[str, Any] = {}
    for ck, per_m in errs_by_city_model.items():
        om = per_m.get("open_meteo_forecast") or []
        if not om:
            continue
        bias = sum(om) / len(om)
        by_city[ck] = {"bias_c": round(bias, 4), "n": len(om)}

    return models_out, by_city


def crowd_bucket_metrics(
    city_filter: Optional[Set[str]] = None,
    ds_gte: Optional[str] = None,
    ds_lte: Optional[str] = None,
) -> Dict[str, Any]:
    """Share of time highest-YES sibling matches truth bucket (per rough outcome rows)."""
    by_event: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in iter_jsonl(C.RESEARCH_MARKET_OUTCOMES_FILE):
        eid = str(row.get("gamma_event_id") or "").strip()
        if not eid:
            continue
        ck = str(row.get("city_key") or "").strip().lower()
        ds = str(row.get("event_date") or "").strip()
        if city_filter is not None and ck not in city_filter:
            continue
        if ds_gte and ds and ds < ds_gte:
            continue
        if ds_lte and ds and ds > ds_lte:
            continue
        by_event[eid].append(row)

    correct = 0
    total = 0
    for _eid, rows in by_event.items():
        priced = [r for r in rows if outcome_gamma_yes_p(r) is not None]
        if len(priced) < 2:
            continue
        leader = max(priced, key=lambda r: float(outcome_gamma_yes_p(r) or 0))
        fit = outcome_fits_truth(leader)
        if fit is None:
            continue
        total += 1
        if fit:
            correct += 1
    if total == 0:
        return {"n_events": 0, "leader_hits_truth_rate": None}
    return {
        "n_events": total,
        "leader_hits_truth_rate": round(correct / total, 4),
    }


def _parse_city_filter(raw: Optional[List[str]]) -> Optional[Set[str]]:
    if not raw:
        return None
    return {str(x).strip().lower() for x in raw if str(x).strip()}


def run_analyze(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build calibration_latest.json from JSONL")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print JSON to stdout only",
    )
    ap.add_argument(
        "--city",
        action="append",
        default=None,
        metavar="CITY",
        help="only these cities (repeatable)",
    )
    ap.add_argument("--start-date", type=str, default="", help="filter event_date >=")
    ap.add_argument("--end-date", type=str, default="", help="filter event_date <=")
    args = ap.parse_args(argv)

    city_filter = _parse_city_filter(args.city)
    ds_gte = str(args.start_date).strip() or None
    ds_lte = str(args.end_date).strip() or None
    truth = build_truth_map(city_filter=city_filter, ds_gte=ds_gte, ds_lte=ds_lte)
    models, by_city = analyze_forecast_errors(truth)
    crowd = crowd_bucket_metrics(city_filter=city_filter, ds_gte=ds_gte, ds_lte=ds_lte)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "truth_samples": len(truth),
        "filters": {
            "cities": sorted(city_filter) if city_filter is not None else None,
            "start_date": ds_gte,
            "end_date": ds_lte,
        },
        "models": models,
        "by_city": by_city,
        "crowd": crowd,
    }
    txt = json.dumps(out, indent=2, ensure_ascii=True) + "\n"
    if args.dry_run:
        print(txt)
        return 0
    save_json_file(C.RESEARCH_CALIBRATION_LATEST_FILE, json.loads(txt))
    C.RESEARCH_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = C.RESEARCH_EXPORTS_DIR / "calibration_latest.csv"
    _write_calibration_csv(csv_path, out)
    print(txt)
    print(json.dumps({"ok": True, "csv": str(csv_path)}, indent=2))
    return 0


def _write_calibration_csv(path: Path, payload: Dict[str, Any]) -> None:
    rows: List[Dict[str, Any]] = []
    for name, spec in (payload.get("models") or {}).items():
        row = {"kind": "model", "name": name}
        row.update(spec)
        rows.append(row)
    for ck, spec in (payload.get("by_city") or {}).items():
        row = {"kind": "city", "name": ck}
        row.update(spec)
        rows.append(row)
    cr = payload.get("crowd") or {}
    rows.append({"kind": "crowd", "name": "leader", **cr})
    if not rows:
        path.write_text("kind,name\n", encoding="utf-8")
        return
    keys: Set[str] = set()
    for r in rows:
        keys.update(r.keys())
    fieldnames = ["kind", "name"] + sorted(k for k in keys if k not in ("kind", "name"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


if __name__ == "__main__":
    import sys

    raise SystemExit(run_analyze(sys.argv[1:]))
