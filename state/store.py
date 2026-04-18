import json
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

from config.constants import ENV_FILE, STATE_FILE


def load_env_file(path: str | Path | None = None) -> None:
    p = Path(path) if path else ENV_FILE
    load_dotenv(p, override=True)


def read_state() -> Dict[str, Any]:
    sf = Path(STATE_FILE)
    empty = {
        "active_trades": {},
        "last_report_sent": "",
        "last_sync_at": "",
        "last_portfolio_fingerprint": "",
        "last_hourly_summary_slot": "",
        "last_status_heartbeat_ts": 0.0,
        "churn_by_market": {},
    }
    if not sf.is_file():
        return dict(empty)
    try:
        with sf.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(empty)
        data.setdefault("active_trades", {})
        data.setdefault("last_report_sent", "")
        data.setdefault("last_sync_at", "")
        data.setdefault("last_portfolio_fingerprint", "")
        data.setdefault("last_hourly_summary_slot", "")
        data.setdefault("last_status_heartbeat_ts", 0.0)
        data.setdefault("churn_by_market", {})
        return data
    except (OSError, json.JSONDecodeError):
        return dict(empty)


def write_state(state: Dict[str, Any]) -> None:
    with Path(STATE_FILE).open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=True)
