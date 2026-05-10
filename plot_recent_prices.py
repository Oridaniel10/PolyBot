"""Plot YES price history per gamma event (all temperature buckets on one axis).

Default: one PNG per event with every sibling bucket from Gamma (missing ring data
shown as dashed y=0). Use --per-market for legacy single-market charts.

Examples:
    python plot_recent_prices.py
    python plot_recent_prices.py --window-min 30 --event-id 12345
    python plot_recent_prices.py --per-market
    python plot_recent_prices.py --watch

Reads samples from strategy.momentum ring (in-process) or data/price_samples/*.jsonl.
Resolves event membership via live Gamma (get_market_by_id + get_markets_for_event_id).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import constants as C
from config.constants import DATA_DIR, PRICE_SAMPLES_DIR, STATE_FILE
from config.settings import get_effective_settings
from polymarket_client import PolymarketClient, gamma_event_ids_for_market
from strategy.bot_runner import build_config
from strategy.decision_core import momentum_entry_candidate_windows
from strategy.momentum_engine import absolute_price_change_in_window

PLOT_DIR = DATA_DIR / "plots"


def _safe_filename(name: str, max_len: int = 80) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return out[:max_len] or "unnamed"


def _short_title(title: str) -> str:
    m = re.search(
        r"in\s+([A-Za-z][A-Za-z\s.'-]+?)\s+be\s+([0-9.\-]+)\s*°?\s*([CFcf])",
        title,
    )
    if not m:
        return _safe_filename(title[:40])
    city = m.group(1).strip().replace(" ", "")
    temp = m.group(2)
    unit = m.group(3).upper()
    return f"{city}_{temp}{unit}"


def _bucket_sort_key(title: str) -> Tuple[int, float]:
    """ascending temperature; 'or higher' / 'or lower' buckets last."""
    t = (title or "").lower()
    if "or higher" in t or "or lower" in t:
        return (2, 9999.0)
    m = re.search(r"be\s+([0-9.]+)\s*°", title, re.I)
    if m:
        try:
            return (0, float(m.group(1)))
        except ValueError:
            pass
    return (1, 0.0)


def _city_date_slug_from_title(title: str) -> str:
    t = title or ""
    city_m = re.search(
        r"highest temperature in\s+([A-Za-z][A-Za-z\s.'-]+?)\s+be\s+",
        t,
        re.I,
    )
    if not city_m:
        city_m = re.search(r"in\s+([A-Za-z][A-Za-z\s.'-]+?)\s+be\s+", t, re.I)
    city = (
        city_m.group(1).strip().replace(" ", "") if city_m else "unknown"
    )
    dm = re.search(r"\bon\s+([A-Za-z]+\s+\d{1,2})(?:,|\?|$|\s)", t, re.I)
    date_part = (
        dm.group(1).replace(" ", "").replace(",", "") if dm else "nodate"
    )
    return _safe_filename(f"{city}_{date_part}")


def _read_disk_samples(window_sec: float, now_ts: float) -> Dict[str, List[Tuple[float, float]]]:
    cutoff = now_ts - window_sec
    by_market: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    if not PRICE_SAMPLES_DIR.is_dir():
        return by_market
    for path in sorted(PRICE_SAMPLES_DIR.glob("*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = float(obj.get("ts") or 0.0)
                    if ts < cutoff:
                        continue
                    mid = str(obj.get("market_id") or "")
                    if not mid:
                        continue
                    by_market[mid].append((ts, float(obj.get("yes") or 0.0)))
        except OSError:
            continue
    for mid in by_market:
        by_market[mid].sort(key=lambda x: x[0])
    return by_market


def _read_ring_samples(
    window_sec: float, now_ts: float
) -> Optional[Dict[str, List[Tuple[float, float]]]]:
    try:
        from strategy.momentum import _price_ring, _price_ring_lock
    except ImportError:
        return None
    cutoff = now_ts - window_sec
    out: Dict[str, List[Tuple[float, float]]] = {}
    with _price_ring_lock:
        for mid, ring in _price_ring.items():
            pts = [(t, p) for (t, p) in ring if t >= cutoff]
            if pts:
                out[str(mid)] = pts
    return out


def _infer_event_slug_from_market_slug(slug: str) -> Optional[str]:
    """strip trailing bucket segment from highest-temp market slug → parent event slug."""
    if not slug.startswith("highest-temperature-in-"):
        return None
    m = re.match(r"^(.*-\d{4})-.+$", slug)
    return m.group(1) if m else None


def _fetch_event_id_by_inferred_slug(
    client: PolymarketClient, inferred_slug: str
) -> str:
    root = client.config.gamma_base_url.rstrip("/")
    try:
        ev = client._request(
            "GET", f"{root}/events/slug/{inferred_slug}", with_auth=False
        )
    except Exception:
        return ""
    if isinstance(ev, dict) and str(ev.get("id") or "").strip():
        return str(ev.get("id")).strip()
    return ""


def _resolve_event_id_for_plot(
    client: PolymarketClient,
    gm: Dict[str, Any],
    slug_event_cache: Dict[str, str],
) -> str:
    """gamma events[] / eventId, else parent event via inferred slug (cached)."""
    evs = gamma_event_ids_for_market(gm)
    if evs:
        return str(evs[0]).strip()
    slug = str(gm.get("slug") or "").strip()
    inferred = _infer_event_slug_from_market_slug(slug)
    if not inferred:
        return ""
    if inferred in slug_event_cache:
        return slug_event_cache[inferred]
    eid = _fetch_event_id_by_inferred_slug(client, inferred)
    slug_event_cache[inferred] = eid
    return eid


def _read_state_titles() -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not STATE_FILE.is_file():
        return out
    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for mid, row in (st.get("active_trades") or {}).items():
        if isinstance(row, dict):
            t = row.get("title") or row.get("question") or ""
            if t:
                out[str(mid)] = str(t)
    return out


def _gamma_resolve_events(
    client: PolymarketClient,
    sample_mids: List[str],
    market_cache: Dict[str, Optional[Dict[str, Any]]],
) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """mid -> event_id; parallel get_market_by_id for uncached mids."""

    def fetch(mid: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        return mid, client.get_market_by_id(mid)

    missing = [m for m in sample_mids if m not in market_cache]
    if missing:
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = {pool.submit(fetch, m): m for m in missing}
            for fut in as_completed(futures):
                mid, gm = fut.result()
                market_cache[mid] = gm

    mid_to_eid: Dict[str, str] = {}
    event_to_sample_mids: Dict[str, List[str]] = defaultdict(list)
    slug_event_cache: Dict[str, str] = {}
    for mid in sample_mids:
        gm = market_cache.get(mid)
        if not gm:
            continue
        evs = gamma_event_ids_for_market(gm)
        if evs:
            eid = str(evs[0]).strip()
            mid_to_eid[mid] = eid
            event_to_sample_mids[eid].append(mid)
            continue
        eid = _resolve_event_id_for_plot(client, gm, slug_event_cache)
        if eid:
            mid_to_eid[mid] = eid
            event_to_sample_mids[eid].append(mid)
    return mid_to_eid, dict(event_to_sample_mids)


def _momentum_line_for_mid(mid: str, now_ts: float, settings: Any) -> str:
    """same windows + absolute_price_change_in_window as decision_core / momentum_engine."""
    wins = momentum_entry_candidate_windows(settings)
    parts: List[str] = []
    for w in wins:
        old_p, new_p, chg_pts = absolute_price_change_in_window(
            mid,
            float(w),
            now_ts,
            min_samples=C.MOMENTUM_MIN_SAMPLE_POINTS,
        )
        mins = max(1, int(round(w / 60.0)))
        if old_p <= 1e-12:
            parts.append(f"{mins}m:∅")
        else:
            pct = (new_p - old_p) / old_p * 100.0 if old_p > 1e-12 else 0.0
            parts.append(f"{mins}mΔ{chg_pts:+.3f}/{pct:+.0f}%")
    return " ".join(parts)


def _plot_single(
    mid: str,
    pts: List[Tuple[float, float]],
    title: str,
    out_path: Path,
    window_sec: float,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    xs = [datetime.fromtimestamp(t, tz=timezone.utc).astimezone() for t, _ in pts]
    ys = [p for _, p in pts]

    fig, ax = plt.subplots(figsize=(9, 4), dpi=110)
    ax.plot(xs, ys, marker="o", linewidth=1.4, markersize=3)
    ax.set_ylim(0, 1)
    ax.set_ylabel("YES price")
    ax.set_xlabel("local time")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    fig.autofmt_xdate()

    n = len(pts)
    if n >= 2:
        old_p = pts[0][1]
        new_p = pts[-1][1]
        delta = new_p - old_p
        pct = (delta / old_p * 100.0) if old_p > 1e-9 else 0.0
        subtitle = f"n={n} · {old_p:.3f} → {new_p:.3f} (Δ {delta:+.3f} / {pct:+.1f}%)"
    else:
        subtitle = f"n={n} (insufficient data)"

    ax.set_title(
        f"{title}\nmid={mid[:24]} · window={int(window_sec / 60)}m · {subtitle}",
        fontsize=10,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def _plot_event_all_buckets(
    event_id: str,
    siblings_ordered: List[Tuple[str, str, List[Tuple[float, float]]]],
    out_path: Path,
    window_sec: float,
    now_ts: float,
    settings: Any,
) -> None:
    """One line per Gamma bucket; empty samples → dashed y=0; momentum annotation."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    t_lo = now_ts - window_sec
    t_hi = now_ts
    xs_edge = [
        datetime.fromtimestamp(t_lo, tz=timezone.utc).astimezone(),
        datetime.fromtimestamp(t_hi, tz=timezone.utc).astimezone(),
    ]

    fig, ax = plt.subplots(figsize=(14, 7), dpi=110)
    mom_lines: List[str] = []

    for mid, title, pts in siblings_ordered:
        label_base = _short_title(title) if title else mid[:12]
        if pts:
            xs = [
                datetime.fromtimestamp(t, tz=timezone.utc).astimezone()
                for t, _ in pts
            ]
            ys = [p for _, p in pts]
            ax.plot(
                xs,
                ys,
                marker=".",
                linewidth=1.3,
                markersize=3,
                label=f"{label_base} (n={len(pts)})",
            )
            mom = _momentum_line_for_mid(mid, now_ts, settings)
            mom_lines.append(f"{label_base} [{mid}] {mom}")
        else:
            ax.plot(
                xs_edge,
                [0.0, 0.0],
                linestyle="--",
                linewidth=1.2,
                color="gray",
                alpha=0.85,
                label=f"{label_base} (no data)",
            )
            mom_lines.append(f"{label_base} [{mid}] (no samples in ring/jsonl)")

    ax.set_ylim(-0.02, 1.0)
    ax.set_ylabel("YES price")
    ax.set_xlabel("local time")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    fig.autofmt_xdate()

    slug = _city_date_slug_from_title(siblings_ordered[0][1]) if siblings_ordered else "event"
    ax.set_title(
        f"Event {event_id} · {slug} · last {int(window_sec / 60)}m\n"
        "(dashed @0 = no rows in price_samples / ring for this bucket in the window)",
        fontsize=11,
    )
    ax.legend(loc="upper left", fontsize=7, framealpha=0.9)

    mom_block = "\n".join(mom_lines[:40])
    if len(mom_lines) > 40:
        mom_block += "\n…"
    fig.text(
        0.02,
        0.01,
        "Momentum (same windows as bot / momentum_entry_candidate_windows):\n" + mom_block,
        fontsize=5,
        family="monospace",
        verticalalignment="bottom",
        transform=fig.transFigure,
        wrap=False,
    )
    fig.subplots_adjust(bottom=0.28)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=[0, 0.08, 1, 0.96])
    fig.savefig(out_path)
    plt.close(fig)


def merge_ring_and_disk_samples(
    window_sec: float, now_ts: float
) -> Dict[str, List[Tuple[float, float]]]:
    """combine in-memory ring + JSONL disk samples for all markets in the window."""
    ring_raw = _read_ring_samples(window_sec, now_ts)
    disk = _read_disk_samples(window_sec, now_ts)
    ring_d: Dict[str, List[Tuple[float, float]]] = ring_raw if ring_raw else {}
    merged: Dict[str, List[Tuple[float, float]]] = {}
    keys = set(ring_d.keys()) | set(disk.keys())
    for mid in keys:
        pts: List[Tuple[float, float]] = []
        if mid in ring_d:
            pts.extend(ring_d[mid])
        if mid in disk:
            pts.extend(disk[mid])
        if not pts:
            continue
        pts.sort(key=lambda x: x[0])
        slim: List[Tuple[float, float]] = []
        for t, p in pts:
            if slim and abs(slim[-1][0] - float(t)) < 1e-6:
                slim[-1] = (float(t), float(p))
            else:
                slim.append((float(t), float(p)))
        merged[mid] = slim
    return merged


def render_post_buy_event_png(
    market_id: str,
    client: PolymarketClient,
    window_min: float,
    out_path: Path,
    *,
    now_ts: Optional[float] = None,
) -> bool:
    """write one event PNG (all sibling buckets) like CLI plot; ring+disk merged."""
    mid = str(market_id or "").strip()
    if not mid:
        return False
    now_ts = now_ts or time.time()
    window_sec = max(60.0, float(window_min) * 60.0)
    samples = merge_ring_and_disk_samples(window_sec, now_ts)
    gm = client.get_market_by_id(mid)
    if not gm:
        return False
    evs = gamma_event_ids_for_market(gm)
    eid = str(evs[0]).strip() if evs else ""
    slug_cache: Dict[str, str] = {}
    if not eid:
        eid = _resolve_event_id_for_plot(client, gm, slug_cache)
    if not eid:
        return False
    full_mkts = client.get_markets_for_event_id(eid)
    if not full_mkts:
        full_mkts = [gm]
    ordered = sorted(
        full_mkts,
        key=lambda m: _bucket_sort_key(str(m.get("question") or "")),
    )
    settings = get_effective_settings()
    sibs: List[Tuple[str, str, List[Tuple[float, float]]]] = []
    for gm2 in ordered:
        if not isinstance(gm2, dict):
            continue
        m2 = str(gm2.get("id") or "").strip()
        if not m2:
            continue
        title = str(gm2.get("question") or gm2.get("title") or m2)
        pts = samples.get(m2, [])
        sibs.append((m2, title, pts))
    if not sibs:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _plot_event_all_buckets(eid, sibs, out_path, window_sec, now_ts, settings)
    try:
        return out_path.is_file() and out_path.stat().st_size > 0
    except OSError:
        return False


def run_once(
    window_min: float,
    event_filter: Optional[str],
    out_root: Path,
    per_market: bool,
) -> Path:
    window_sec = float(window_min) * 60.0
    now = time.time()
    settings = get_effective_settings()

    samples = _read_ring_samples(window_sec, now)
    source = "ring"
    if samples is None or not samples:
        samples = _read_disk_samples(window_sec, now)
        source = "disk"

    if not samples:
        print(f"[plot] no samples found (ring empty and {PRICE_SAMPLES_DIR})")
        return out_root

    client = PolymarketClient(build_config())
    market_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    sample_mids = sorted(samples.keys())

    mid_to_eid: Dict[str, str] = {}
    if event_filter:
        events_to_plot = [event_filter.strip()]
    else:
        mid_to_eid, _ = _gamma_resolve_events(client, sample_mids, market_cache)
        events_to_plot = sorted({e for e in mid_to_eid.values() if e})

    if not events_to_plot:
        print(
            "[plot] no event_id resolved from sample market_ids "
            "(check Gamma get_market_by_id / network)"
        )
        return out_root

    titles_held = _read_state_titles()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = out_root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    n_market_png = 0
    n_event_png = 0

    if per_market:
        for mid, pts in samples.items():
            gm = market_cache.get(mid)
            if gm is None:
                gm = client.get_market_by_id(mid)
                market_cache[mid] = gm
            ttl = ""
            if gm:
                ttl = str(gm.get("question") or gm.get("title") or "")
            if not ttl:
                ttl = titles_held.get(mid) or f"market {mid}"
            slug = _short_title(ttl) if ttl else _safe_filename(mid)
            fname = f"{_safe_filename(mid)[:18]}__{slug}.png"
            _plot_single(mid, pts, ttl, out_dir / fname, window_sec)
            n_market_png += 1

    event_cache_lists: Dict[str, List[Dict[str, Any]]] = {}
    for eid in events_to_plot:
        if not eid:
            continue
        full = client.get_markets_for_event_id(eid)
        if not full:
            mids_here = [m for m, e in mid_to_eid.items() if e == eid]
            if not mids_here:
                continue
            synthetic = []
            for mm in mids_here:
                gm = market_cache.get(mm) or client.get_market_by_id(mm)
                market_cache[mm] = gm
                if gm:
                    synthetic.append(gm)
            full = synthetic
        event_cache_lists[eid] = full

    for eid in sorted(event_cache_lists.keys()):
        full_mkts = event_cache_lists[eid]
        ordered = sorted(full_mkts, key=lambda m: _bucket_sort_key(str(m.get("question") or "")))

        sibs: List[Tuple[str, str, List[Tuple[float, float]]]] = []
        title0 = ""
        for gm in ordered:
            if not isinstance(gm, dict):
                continue
            mid = str(gm.get("id") or "").strip()
            if not mid:
                continue
            title = str(gm.get("question") or gm.get("title") or mid)
            if not title0:
                title0 = title
            pts = samples.get(mid, [])
            sibs.append((mid, title, pts))

        if not sibs:
            continue

        slug = _city_date_slug_from_title(title0) if title0 else "event"
        fname = f"_event_{_safe_filename(eid)}__{slug}.png"
        _plot_event_all_buckets(
            eid,
            sibs,
            out_dir / fname,
            window_sec,
            now,
            settings,
        )
        n_event_png += 1

    print(
        f"[plot] out={out_dir} source={source} window_min={int(window_min)} "
        f"event_pngs={n_event_png} per_market_pngs={n_market_png}"
    )
    return out_dir


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--window-min", type=float, default=15.0, help="lookback in minutes")
    ap.add_argument("--event-id", type=str, default=None, help="only this gamma event")
    ap.add_argument("--out-dir", type=str, default=str(PLOT_DIR), help="output root directory")
    ap.add_argument(
        "--per-market",
        action="store_true",
        help="also write one PNG per market with samples (legacy)",
    )
    ap.add_argument("--watch", action="store_true", help="re-run every interval seconds")
    ap.add_argument("--watch-interval", type=int, default=30, help="seconds between watch runs")
    args = ap.parse_args(argv)

    out_root = Path(args.out_dir)
    if args.watch:
        try:
            while True:
                run_once(args.window_min, args.event_id, out_root, args.per_market)
                time.sleep(max(5, int(args.watch_interval)))
        except KeyboardInterrupt:
            print("\n[plot] stopped")
            return 0
    else:
        run_once(args.window_min, args.event_id, out_root, args.per_market)
    return 0


if __name__ == "__main__":
    sys.exit(main())
