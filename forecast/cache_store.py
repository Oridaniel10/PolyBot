"""Persist forecast preview payload for dashboard + Telegram portfolio lines."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from config.constants import DATA_DIR, FORECAST_CACHE_FILE


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def save_forecast_cache(payload: Dict[str, Any]) -> None:
    ensure_data_dir()
    body = {
        **payload,
        "cache_written_unix": time.time(),
    }
    text = json.dumps(body, ensure_ascii=True, separators=(",", ":"))
    tmp = FORECAST_CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, FORECAST_CACHE_FILE)


def save_forecast_cache_after_gamma_fetch(
    payload: Dict[str, Any], *, gamma_market_count: int
) -> bool:
    """
    Persists forecast cache. If Gamma returned no markets but disk already has
    a non-empty snapshot, leaves the file unchanged and returns False.
    """
    if gamma_market_count <= 0:
        prev = load_forecast_cache()
        if isinstance(prev, dict):
            pg = prev.get("groups")
            if isinstance(pg, list) and len(pg) > 0:
                return False
    save_forecast_cache(payload)
    return True


def load_forecast_cache() -> Optional[Dict[str, Any]]:
    if not FORECAST_CACHE_FILE.is_file():
        return None
    try:
        raw = FORECAST_CACHE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def forecast_cache_age_sec() -> Optional[float]:
    data = load_forecast_cache()
    if not data:
        return None
    ts = data.get("cache_written_unix")
    try:
        return max(0.0, time.time() - float(ts))
    except (TypeError, ValueError):
        return None
