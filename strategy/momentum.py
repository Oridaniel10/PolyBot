"""Price sample storage backed by Redis sorted sets.

Architecture:
- Every price sample is written to BOTH Redis (fast reads, TTL cleanup) and
  an append-only JSONL file on disk (archival / disaster recovery).
- Reads always come from Redis — O(log N) ZRANGEBYSCORE per market.
- On startup, `warm_ring_buffer_from_disk()` bulk-loads today's + yesterday's
  JSONL into Redis so momentum calculations work immediately after restart.
- JSONL files are trimmed periodically for disk hygiene but never re-read
  after warm-up.
"""

import json
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config.constants import (
    PRICE_SAMPLE_MAX_ENTRIES_PER_MARKET,
    PRICE_SAMPLE_RETENTION_DAYS,
    PRICE_SAMPLES_DIR,
    TIMEZONE,
)
from config.settings import RuntimeSettings
from strategy.probability import parse_market_probability
from strategy import redis_store

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]

PRICE_SAMPLE_WINDOW_SECONDS = 2 * 60 * 60
PRICE_SAMPLE_TRIM_INTERVAL_SECONDS = 120
PRICE_SAMPLE_ENOSPC_RETRY_SECONDS = 60

_last_trim_ts = 0.0
_writes_blocked_until_ts = 0.0
_ring_warmed = False  # kept for API compatibility; True after warm-up


@dataclass(frozen=True)
class SampleWindow:
    points: List[Tuple[float, float]]
    count: int
    oldest_ts: float
    newest_ts: float
    oldest_price: float
    newest_price: float
    span_sec: float


def prune_old_price_sample_files() -> None:
    PRICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.utcnow().date() - timedelta(days=PRICE_SAMPLE_RETENTION_DAYS)
    for p in PRICE_SAMPLES_DIR.glob("*.jsonl"):
        try:
            stem = p.stem
            day = datetime.strptime(stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            try:
                p.unlink()
            except OSError:
                pass


def _sample_path_for_now() -> Path:
    PRICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    if ZoneInfo is None:
        d = datetime.utcnow().strftime("%Y-%m-%d")
    else:
        d = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    return PRICE_SAMPLES_DIR / f"{d}.jsonl"


# ── warm-up: load JSONL → Redis once at startup ──────────────────────

def warm_ring_buffer_from_disk() -> int:
    """Bulk-load recent JSONL samples into Redis.

    Called once at startup so momentum calculations work immediately.
    Returns the number of samples loaded.
    """
    global _ring_warmed
    if _ring_warmed:
        return 0
    now_ts = time.time()
    cutoff = now_ts - float(PRICE_SAMPLE_WINDOW_SECONDS)
    loaded = 0
    PRICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    batch: List[Tuple[str, float, float]] = []
    seen_per_market: Dict[str, float] = {}  # market_id → last seen ts (dedup)

    for p in sorted(PRICE_SAMPLES_DIR.glob("*.jsonl")):
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = float(o.get("ts") or 0)
                    if ts < cutoff:
                        continue
                    mid = str(o.get("market_id") or "").strip()
                    if not mid:
                        continue
                    yes = float(o.get("yes") or 0)
                    prev_ts = seen_per_market.get(mid, -1.0)
                    if ts > prev_ts + 0.01:
                        batch.append((mid, yes, ts))
                        seen_per_market[mid] = ts
                        loaded += 1
        except OSError:
            continue

    if batch:
        redis_store.save_prices_pipeline(batch)

    _ring_warmed = True
    n_markets = len(seen_per_market)
    print(
        f"[momentum] Redis warmed: {loaded} samples across {n_markets} markets "
        f"(cutoff {PRICE_SAMPLE_WINDOW_SECONDS}s)"
    )
    return loaded


# ── append: write to Redis + disk (archival) ─────────────────────────

def append_price_sample(
    market_id: str, yes_price: float, ts: float | None = None
) -> None:
    global _writes_blocked_until_ts
    if not market_id:
        return
    t = ts if ts is not None else time.time()
    if t < _writes_blocked_until_ts:
        return

    # 1. Redis (primary — instant, TTL-managed)
    redis_store.save_price(market_id, float(yes_price), t)

    # 2. Disk — append-only JSONL (archival / fallback)
    row = {"market_id": market_id, "ts": t, "yes": float(yes_price)}
    path = _sample_path_for_now()
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")
    except OSError as err:
        if getattr(err, "errno", None) == 28:
            _writes_blocked_until_ts = t + PRICE_SAMPLE_ENOSPC_RETRY_SECONDS
            print(
                "[momentum] sample write paused: no disk space "
                f"(retry in {PRICE_SAMPLE_ENOSPC_RETRY_SECONDS}s)"
            )
        else:
            raise


# ── read: from Redis ──────────────────────────────────────────────────

def load_samples_for_market(
    market_id: str, window_sec: float, now_ts: float | None = None
) -> List[Tuple[float, float]]:
    """Return (ts, price) pairs for market_id within [now - window_sec, now].

    Includes one anchor point before the cutoff so rise calculations have an
    old price baseline.  Reads from Redis (sub-millisecond for typical sizes).
    """
    now_ts = now_ts if now_ts is not None else time.time()
    return redis_store.get_price_window(market_id, window_sec, now_ts)


def load_sample_window_for_market(
    market_id: str, window_sec: float, now_ts: float | None = None
) -> SampleWindow:
    points = load_samples_for_market(market_id, window_sec, now_ts)
    if not points:
        return SampleWindow([], 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    oldest_ts, oldest_price = points[0]
    newest_ts, newest_price = points[-1]
    return SampleWindow(
        points=points,
        count=len(points),
        oldest_ts=float(oldest_ts),
        newest_ts=float(newest_ts),
        oldest_price=float(oldest_price),
        newest_price=float(newest_price),
        span_sec=max(0.0, float(newest_ts) - float(oldest_ts)),
    )


def momentum_window_rise(
    market_id: str, window_min: int, now_ts: float | None = None
) -> Tuple[float, float, float]:
    """
    returns (oldest_yes_in_window, newest_yes_in_window, rise_ratio).
    rise_ratio = (new - old) / old if old > 0 else 0.
    """
    wsec = float(window_min) * 60.0
    series = load_samples_for_market(market_id, wsec, now_ts)
    if len(series) < 2:
        return 0.0, 0.0, 0.0
    old_yes = series[0][1]
    new_yes = series[-1][1]
    if old_yes <= 1e-9:
        return old_yes, new_yes, 0.0
    rise = (new_yes - old_yes) / old_yes
    return old_yes, new_yes, rise


def max_peer_yes_rise_ratio(
    peer_market_ids: List[str], window_min: int, now_ts: float | None = None
) -> float:
    """max fractional rise among peers' yes price over window (0..+inf scale)."""
    wsec = float(window_min) * 60.0
    now_ts = now_ts if now_ts is not None else time.time()
    best = 0.0
    for pid in peer_market_ids:
        pid = str(pid).strip()
        if not pid:
            continue
        series = load_samples_for_market(pid, wsec, now_ts)
        if len(series) < 2:
            continue
        a, b = series[0][1], series[-1][1]
        if a <= 1e-9:
            continue
        rise = (b - a) / a
        if rise > best:
            best = rise
    return best


def max_peer_yes_drop_ratio(
    peer_market_ids: List[str], window_min: int, now_ts: float | None = None
) -> float:
    """max fractional drop among peers' yes price over window (0..1)."""
    wsec = float(window_min) * 60.0
    now_ts = now_ts if now_ts is not None else time.time()
    worst = 0.0
    for pid in peer_market_ids:
        pid = str(pid).strip()
        if not pid:
            continue
        series = load_samples_for_market(pid, wsec, now_ts)
        if len(series) < 2:
            continue
        a, b = series[0][1], series[-1][1]
        if a <= 1e-9:
            continue
        drop = (a - b) / a
        if drop > worst:
            worst = drop
    return worst


def trim_price_samples_file(
    path: Path, max_per_market: int = PRICE_SAMPLE_MAX_ENTRIES_PER_MARKET
) -> None:
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    cutoff_ts = time.time() - float(PRICE_SAMPLE_WINDOW_SECONDS)
    by_market: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        mid = str(obj.get("market_id") or "")
        ts = float(obj.get("ts") or 0.0)
        if mid:
            if ts + 1e-9 < cutoff_ts:
                continue
            by_market[mid].append((ts, stripped))
    kept: List[str] = []
    for mid in sorted(by_market):
        entries = sorted(by_market[mid], key=lambda x: x[0])
        kept.extend([row for _, row in entries[-max_per_market:]])
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=path.stem
        )
        with open(fd, "w", encoding="utf-8") as f:
            for entry in kept:
                f.write(entry + "\n")
        Path(tmp).replace(path)
    except OSError:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass


def trim_price_samples_if_due(now_ts: float | None = None) -> None:
    global _last_trim_ts
    now_ts = now_ts if now_ts is not None else time.time()
    if now_ts - _last_trim_ts < PRICE_SAMPLE_TRIM_INTERVAL_SECONDS:
        return
    _last_trim_ts = now_ts
    # Trim JSONL archival file
    path = _sample_path_for_now()
    trim_price_samples_file(path)
    # Clean Redis entries older than TTL
    redis_store.cleanup_all_markets()


def record_samples_for_market_dicts(
    markets: List[Dict[str, Any]],
    clob_prices: Optional[Dict[str, float]] = None,
) -> None:
    now_ts = time.time()
    seen: Set[str] = set()
    batch: List[Tuple[str, float, float]] = []
    disk_rows: List[str] = []

    for m in markets:
        mid = str(m.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        # prefer fresh CLOB price (injected by loop.py) over stale Gamma outcomePrices
        if clob_prices and mid in clob_prices and clob_prices[mid] > 1e-6:
            p = clob_prices[mid]
        else:
            p = parse_market_probability(m)
        batch.append((mid, float(p), now_ts))
        row = {"market_id": mid, "ts": now_ts, "yes": float(p)}
        disk_rows.append(json.dumps(row, ensure_ascii=True, separators=(",", ":")))

    if batch:
        redis_store.save_prices_pipeline(batch)

    if disk_rows:
        path = _sample_path_for_now()
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write("\n".join(disk_rows) + "\n")
        except OSError as err:
            if getattr(err, "errno", None) == 28:
                global _writes_blocked_until_ts
                _writes_blocked_until_ts = now_ts + PRICE_SAMPLE_ENOSPC_RETRY_SECONDS
                print(
                    "[momentum] sample write paused: no disk space "
                    f"(retry in {PRICE_SAMPLE_ENOSPC_RETRY_SECONDS}s)"
                )

    trim_price_samples_if_due(now_ts)


def momentum_entry_allowed(
    market_id: str,
    current_yes: float,
    settings: RuntimeSettings,
    now_ts: float | None = None,
) -> bool:
    if not settings.enable_momentum:
        return False
    _, new_yes, rise = momentum_window_rise(
        market_id, settings.momentum_window_min, now_ts
    )
    if new_yes <= 0:
        new_yes = current_yes
    if rise < settings.momentum_rise - 1e-9:
        return False
    if current_yes < settings.momentum_min_price - 1e-9:
        return False
    return True


def momentum_effective_buy_max(base_max: float, settings: RuntimeSettings) -> float:
    if not settings.enable_momentum:
        return base_max
    return max(base_max, min(settings.momentum_max_entry, 0.99))
