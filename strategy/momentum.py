import json
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from config.constants import (
    PRICE_SAMPLE_MAX_ENTRIES_PER_MARKET,
    PRICE_SAMPLE_RETENTION_DAYS,
    PRICE_SAMPLES_DIR,
    TIMEZONE,
)
from config.settings import RuntimeSettings
from strategy.probability import parse_market_probability
from strategy.time_utils import now_in_report_timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]


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


def append_price_sample(
    market_id: str, yes_price: float, ts: float | None = None
) -> None:
    if not market_id:
        return
    t = ts if ts is not None else time.time()
    row = {"market_id": market_id, "ts": t, "yes": float(yes_price)}
    path = _sample_path_for_now()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")


def load_samples_for_market(
    market_id: str, window_sec: float, now_ts: float | None = None
) -> List[Tuple[float, float]]:
    now_ts = now_ts if now_ts is not None else time.time()
    cutoff = now_ts - window_sec
    out: List[Tuple[float, float]] = []
    PRICE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
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
                    if str(o.get("market_id") or "") != market_id:
                        continue
                    ts = float(o.get("ts") or 0)
                    yes = float(o.get("yes") or 0)
                    if ts >= cutoff:
                        out.append((ts, yes))
        except OSError:
            continue
    out.sort(key=lambda x: x[0])
    return out


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
    by_market: Dict[str, List[str]] = defaultdict(list)
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        mid = str(obj.get("market_id") or "")
        if mid:
            by_market[mid].append(stripped)
    kept: List[str] = []
    for mid in sorted(by_market):
        entries = by_market[mid]
        kept.extend(entries[-max_per_market:])
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


def trim_price_samples_async() -> None:
    path = _sample_path_for_now()
    t = threading.Thread(
        target=trim_price_samples_file, args=(path,), daemon=True
    )
    t.start()


def record_samples_for_market_dicts(markets: List[Dict[str, Any]]) -> None:
    now_ts = time.time()
    seen: Set[str] = set()
    for m in markets:
        mid = str(m.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        p = parse_market_probability(m)
        append_price_sample(mid, p, now_ts)
    trim_price_samples_async()


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
