import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from config.constants import FLOW_SAMPLES_FILE


def _load_samples(window_sec: float, now_ts: float) -> List[Dict[str, Any]]:
    if not FLOW_SAMPLES_FILE.is_file():
        return []
    cutoff = now_ts - window_sec
    out: List[Dict[str, Any]] = []
    try:
        with FLOW_SAMPLES_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(o, dict):
                    continue
                try:
                    ts = float(o.get("ts") or 0)
                except (TypeError, ValueError):
                    continue
                if ts >= cutoff:
                    out.append(o)
    except OSError:
        return []
    return out


def _split_means(
    rows: List[Tuple[float, float]]
) -> Tuple[Optional[float], Optional[float]]:
    """rows sorted by ts: (ts, yes_mid) -> early mean, late mean."""
    if len(rows) < 4:
        return None, None
    rows = sorted(rows, key=lambda x: x[0])
    half = max(2, len(rows) // 2)
    early = rows[:half]
    late = rows[half:]
    em = sum(y for _, y in early) / len(early)
    lm = sum(y for _, y in late) / len(late)
    return em, lm


def flow_peer_recommends_exit(
    held_market_id: str,
    event_id: str,
    settings: Any,
    *,
    now_ts: float,
) -> bool:
    """
    True if held YES mid dropped while another market in the same event rose,
    over the configured window (from flow_samples.jsonl).
    """
    if not getattr(settings, "enable_flow_peer_exit", False):
        return False
    window = float(getattr(settings, "flow_peer_window_sec", 600))
    drop_need = float(getattr(settings, "flow_peer_surge_drop", 0.12))
    rise_need = float(getattr(settings, "flow_peer_surge_rise", 0.10))

    samples = _load_samples(window, now_ts)
    ev_rows = [s for s in samples if str(s.get("event_id") or "") == str(event_id)]
    if len(ev_rows) < 8:
        return False

    by_mid: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for s in ev_rows:
        mid = str(s.get("market_id") or "").strip()
        if not mid:
            continue
        try:
            y = float(s.get("yes_mid") or 0)
        except (TypeError, ValueError):
            continue
        try:
            ts = float(s.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        by_mid[mid].append((ts, y))

    held = str(held_market_id).strip()
    held_early, held_late = _split_means(by_mid.get(held, []))
    if held_early is None or held_late is None:
        return False
    if held_early - held_late < drop_need - 1e-9:
        return False

    for mid, series in by_mid.items():
        if mid == held:
            continue
        pe, pl = _split_means(series)
        if pe is None or pl is None:
            continue
        if pl - pe >= rise_need - 1e-9 and pl > held_late + 0.02:
            return True
    return False
