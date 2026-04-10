import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.constants import PNL_LEDGER_FILE, TIMEZONE

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[assignment]


def _ledger_path() -> Path:
    PNL_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    return PNL_LEDGER_FILE


def append_ledger_row(row: Dict[str, Any]) -> None:
    path = _ledger_path()
    line = json.dumps(row, ensure_ascii=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _parse_ts(raw: str) -> Optional[datetime]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _now_tz() -> datetime:
    if ZoneInfo is None:
        return datetime.utcnow()
    return datetime.now(ZoneInfo(TIMEZONE))


def read_ledger_rows_since(cutoff: datetime) -> List[Dict[str, Any]]:
    path = _ledger_path()
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = _parse_ts(str(row.get("ts_iso") or row.get("ts") or ""))
                if ts is None:
                    continue
                if ts >= cutoff.replace(tzinfo=ts.tzinfo) if ts.tzinfo else ts >= cutoff:
                    out.append(row)
    except OSError:
        return []
    return out


def _empty_bucket() -> Dict[str, Any]:
    return {
        "buy_notional_usd": 0.0,
        "sell_notional_usd": 0.0,
        "est_pnl_usd": 0.0,
        "trade_count": 0,
    }


def pnl_summary_day_week() -> Dict[str, Any]:
    now = _now_tz()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=6)

    def bucket(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        buy_usd = 0.0
        sell_usd = 0.0
        est_pnl = 0.0
        for r in rows:
            side = str(r.get("side") or "").lower()
            usd = float(r.get("usd") or 0)
            if side == "buy":
                buy_usd += usd
            elif side == "sell":
                sell_usd += usd
                est_pnl += float(r.get("est_pnl_usd") or 0)
        return {
            "buy_notional_usd": round(buy_usd, 2),
            "sell_notional_usd": round(sell_usd, 2),
            "est_pnl_usd": round(est_pnl, 2),
            "trade_count": len(rows),
        }

    try:
        day_rows = read_ledger_rows_since(day_start)
        week_rows = read_ledger_rows_since(week_start)
        return {
            "day": bucket(day_rows),
            "week": bucket(week_rows),
            "as_of": now.isoformat(),
            "error": None,
        }
    except (OSError, ValueError, TypeError) as exc:
        return {
            "day": _empty_bucket(),
            "week": _empty_bucket(),
            "as_of": now.isoformat(),
            "error": repr(exc),
        }
